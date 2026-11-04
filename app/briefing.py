import asyncio
from dataclasses import dataclass

import httpx

from .budget import Budget
from .cache import TtlCache, cached
from .config import Config
from .errors import HttpError, UpstreamError
from .llm import ask_llm, ask_llm_stream
from .weather import get_forecast, get_place_by_id, search_places
from .wikipedia import get_nearby, get_wikipedia

MAX_ALTERNATIVES = 4


@dataclass
class Deps:
    """Everything a request needs. Plain data, so tests can build it with fakes (see make_deps)."""
    client: httpx.AsyncClient
    config: Config
    caches: dict  # name -> TtlCache: "geo", "weather", "wiki", "nearby"
    budget: Budget


def make_deps(config: Config, client: httpx.AsyncClient) -> Deps:
    caches = {name: TtlCache() for name in ("geo", "weather", "wiki", "nearby")}
    return Deps(client=client, config=config, caches=caches, budget=Budget(config.llm_daily_cap))
BUDGET_WARNING = {
    "service": "LLM", "kind": "budget",
    "message": "AI briefings are paused: the demo's daily usage cap was reached. The live data below is still current.",
}


async def resolve_place(city, place_id, deps):
    """Pick the place to brief. Returns (place, matches): `matches` is every geocoder hit for the city
    (best first), used to offer alternatives. An explicit id pins the exact place."""
    cfg, caches, client = deps.config, deps.caches, deps.client

    async def search():
        value, _ = await cached(caches["geo"], f"s:{city.lower()}", cfg.geo_ttl_s, lambda: search_places(client, city))
        return value

    if place_id:
        place, _ = await cached(caches["geo"], f"id:{place_id}", cfg.geo_ttl_s, lambda: get_place_by_id(client, place_id))
        matches = []
        if city:  # alternatives are a nicety: if the (usually cached) search fails, show none
            try:
                matches = await search()
            except UpstreamError:
                matches = []
        return place, matches

    matches = await search()
    if not matches:
        raise HttpError(404, f'Couldn\'t find a city called "{city}"', "city_not_found")
    return matches[0], matches


def pick_alternatives(matches, place, limit=MAX_ALTERNATIVES):
    """The other matches, as ids and labels only (clients never get coordinates)."""
    return [{k: m[k] for k in ("id", "name", "region", "country")} for m in matches if m["id"] != place["id"]][:limit]


async def fetch_forecast(place, deps):
    """Required step. Returns (weather, warnings); a stale cache hit adds a warning."""
    weather, stale = await cached(
        deps.caches["weather"], f"fc:{place['id']}", deps.config.weather_ttl_s, lambda: get_forecast(deps.client, place)
    )
    warnings = []
    if stale:
        warnings.append({"service": "Open-Meteo", "kind": "stale", "message": "Open-Meteo is unavailable; showing cached weather."})
    return weather, warnings


async def fetch_wikipedia(place, deps):
    """Optional step: the summary (the article must sit near the coordinates) and nearby sights (by coordinates). They don't depend on each
    other, so run together. Returns (wiki, nearby, warnings) with at most one warning per service."""
    cfg, caches, client = deps.config, deps.caches, deps.client
    wiki_result, nearby_result = await asyncio.gather(
        cached(caches["wiki"], str(place["id"]), cfg.wiki_ttl_s, lambda: get_wikipedia(client, place)),
        cached(caches["nearby"], str(place["id"]), cfg.wiki_ttl_s, lambda: get_nearby(client, place)),
        return_exceptions=True,
    )
    wiki, nearby, warnings = None, [], []
    for kind, result in (("wiki", wiki_result), ("nearby", nearby_result)):
        if isinstance(result, UpstreamError):
            if not any(w["service"] == result.service for w in warnings):
                warnings.append(result.as_warning())
        elif isinstance(result, BaseException):
            raise result
        elif kind == "wiki":
            wiki = result[0]
        else:
            nearby = result[0]
    return wiki, nearby, warnings


def build_context(weather, wiki, nearby):
    """What the LLM sees. Keys double as the source names the model cites."""
    wikipedia = None
    if wiki or nearby:
        wikipedia = {}
        if wiki:
            wikipedia.update(title=wiki["title"], description=wiki["description"], summary=wiki["extract"])
        if nearby:
            wikipedia["nearby_places"] = [{"title": n["title"], "description": n["description"]} for n in nearby]
    # The source URL is for the UI's "view source" link; the model doesn't need it.
    return {"Open-Meteo": {k: v for k, v in weather.items() if k != "source_url"}, "Wikipedia": wikipedia}


async def generate_answer(context, deps):
    """Optional step. Returns (answer or None, warnings). Over the global budget no LLM call is made."""
    if not deps.budget.try_consume():
        return None, [BUDGET_WARNING]
    try:
        return await ask_llm(deps.client, deps.config.llm, context), []
    except UpstreamError as err:
        return None, [err.as_warning()]


async def gather_briefing(city, place_id, deps):
    """Every step except the model. Returns (briefing, context): the briefing has `answer` None and the
    warnings so far; the context is what the model is shown.
      resolve place (Open-Meteo geocode) -> forecast (required) -> Wikipedia summary + nearby (optional).
    Open-Meteo failure aborts the request; Wikipedia failure degrades it with a warning."""
    place, matches = await resolve_place(city, place_id, deps)
    weather, forecast_warnings = await fetch_forecast(place, deps)
    wiki, nearby, wiki_warnings = await fetch_wikipedia(place, deps)
    briefing = {
        "city": weather["place"], "weather": weather, "wikipedia": wiki, "nearby": nearby,
        "answer": None, "alternatives": pick_alternatives(matches, place),
        "warnings": forecast_warnings + wiki_warnings,
    }
    return briefing, build_context(weather, wiki, nearby)


async def build_briefing(city, place_id, deps):
    """The whole workflow in one response: gather_briefing, then the LLM picks things to do that fit the
    forecast (optional: failure gives `answer` None and a warning)."""
    briefing, context = await gather_briefing(city, place_id, deps)
    briefing["answer"], llm_warnings = await generate_answer(context, deps)
    briefing["warnings"] += llm_warnings
    return briefing


async def stream_answer(context, deps):
    """The model step as events: ("delta", text) pieces, then ("done", answer). If it fails, a single
    ("warning", warning) instead (text may already have been sent). Same budget rule as generate_answer."""
    if not deps.budget.try_consume():
        yield "warning", BUDGET_WARNING
        return
    try:
        async for event in ask_llm_stream(deps.client, deps.config.llm, context):
            yield event
    except UpstreamError as err:
        yield "warning", err.as_warning()
