"""The workflow, one step at a time. No FastAPI involved."""
import httpx
import pytest

from app.briefing import (BUDGET_WARNING, build_briefing, build_context, fetch_forecast, fetch_wikipedia, generate_answer,
                          pick_alternatives, resolve_place)
from app.errors import HttpError, UpstreamError

from ..conftest import FORECAST, GEO, LISBON, SAN_DIEGO, json_response, make_test_deps, run

GEOCODE = "geocoding-api.open-meteo.com"
PLACE = {"id": 1, "name": "Lisbon", "region": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}
WEATHER = {"source_url": "https://u", "place": {"id": 1, "name": "Lisbon", "region": "Lisbon", "country": "Portugal"}, "current": {}, "forecast": []}
WIKI = {"title": "Lisbon", "description": "Capital", "extract": "Lisbon is the capital.", "url": "https://w"}
NEARBY = [{"title": "Castle", "description": "Historic castle", "url": "https://c"}]
GEO_SD = {"results": SAN_DIEGO}


def hosts(calls):
    return [c.url.host for c in calls]


# ---- pick_alternatives / build_context (pure)

def test_pick_alternatives_excludes_the_chosen_place_keeps_order_and_hides_coordinates():
    matches = [{"id": i, "name": "N", "region": "R", "country": "C", "latitude": 1, "longitude": 2} for i in range(1, 7)]
    alts = pick_alternatives(matches, matches[2])
    assert [a["id"] for a in alts] == [1, 2, 4, 5]  # capped at 4
    assert set(alts[0]) == {"id", "name", "region", "country"}


def test_pick_alternatives_with_no_other_matches():
    assert pick_alternatives([PLACE], PLACE) == []


def test_build_context_describes_only_the_sources_we_have():
    summary = {"title": "Lisbon", "description": "Capital", "summary": "Lisbon is the capital."}
    nearby = {"nearby_places": [{"title": "Castle", "description": "Historic castle"}]}
    for wiki, near, expected in [(None, [], None), (WIKI, [], summary), (None, NEARBY, nearby), (WIKI, NEARBY, {**summary, **nearby})]:
        assert build_context(WEATHER, wiki, near)["Wikipedia"] == expected


def test_build_context_hides_urls_from_the_model_without_mutating_the_weather():
    ctx = build_context(WEATHER, WIKI, NEARBY)
    assert "source_url" not in ctx["Open-Meteo"] and "url" not in str(ctx["Wikipedia"])
    assert "source_url" in WEATHER


# ---- resolve_place

def test_resolve_by_city_picks_the_best_match_and_returns_all_matches():
    deps, _ = make_test_deps({GEOCODE: lambda r: json_response(GEO_SD)})
    place, matches = run(resolve_place("San Diego", None, deps))
    assert place["region"] == "California" and len(matches) == 3


def test_resolve_with_no_matches_is_a_404():
    deps, calls = make_test_deps({GEOCODE: lambda r: json_response({})})
    with pytest.raises(HttpError) as e:
        run(resolve_place("Nowhereville", None, deps))
    assert (e.value.status, e.value.code) == (404, "city_not_found")


def test_resolve_by_id_pins_the_place_and_still_lists_the_other_matches():
    deps, _ = make_test_deps({GEOCODE: lambda r: json_response(SAN_DIEGO[1] if "/v1/get" in str(r.url) else GEO_SD)})
    place, matches = run(resolve_place("San Diego", "4726311", deps))
    assert place["region"] == "Texas" and len(matches) == 3


def test_resolve_by_id_alone_does_not_search():
    deps, calls = make_test_deps({GEOCODE: lambda r: json_response(LISBON)})
    place, matches = run(resolve_place("", "2267057", deps))
    assert matches == [] and all("/v1/get" in str(c.url) for c in calls)


def test_a_failed_alternatives_search_does_not_fail_an_id_lookup():
    def route(request):
        return json_response(LISBON) if "/v1/get" in str(request.url) else json_response({}, 429, {"retry-after": "60"})

    deps, _ = make_test_deps({GEOCODE: route})
    place, matches = run(resolve_place("Lisbon", "2267057", deps))
    assert place["name"] == "Lisbon" and matches == []


def test_geocoding_is_cached():
    deps, calls = make_test_deps()
    run(resolve_place("Lisbon", None, deps))
    run(resolve_place("lisbon", None, deps))  # keys are case-insensitive
    assert len(calls) == 1


# ---- fetch_forecast

def test_forecast_is_cached_and_not_stale_when_fresh():
    deps, calls = make_test_deps()
    for _ in range(2):
        weather, warnings = run(fetch_forecast(PLACE, deps))
    assert warnings == [] and len(calls) == 1


def test_stale_forecast_is_served_with_a_warning_when_the_upstream_fails():
    state = {"fail": False}
    deps, _ = make_test_deps(
        {"api.open-meteo.com": lambda r: json_response({}, 502) if state["fail"] else json_response(FORECAST)},
        weather_ttl_s=-1,
    )
    run(fetch_forecast(PLACE, deps))
    state["fail"] = True
    weather, warnings = run(fetch_forecast(PLACE, deps))
    assert weather["current"]["temp_f"] == 70
    assert [(w["service"], w["kind"]) for w in warnings] == [("Open-Meteo", "stale")]


def test_forecast_failure_without_a_cache_is_raised():
    deps, _ = make_test_deps({"api.open-meteo.com": lambda r: json_response({}, 502)})
    with pytest.raises(UpstreamError):
        run(fetch_forecast(PLACE, deps))


# ---- fetch_wikipedia: warning rules

def wiki_routes(summary_fails=False, nearby_fails=False, summary=None):
    from ..conftest import NEARBY as NEARBY_PAGES, SEARCH, SUMMARY

    def route(request):
        url = str(request.url)
        if "/w/api.php" in url:
            return json_response({}, 502) if nearby_fails else json_response(NEARBY_PAGES)
        if summary_fails:
            return json_response({}, 502)
        return json_response(SEARCH if "/search/page" in url else summary or SUMMARY)

    return {"en.wikipedia.org": route}


def test_wikipedia_failures_degrade_independently_and_warn_once():
    # (summary fails, nearby fails) -> (has wiki, has nearby, number of warnings)
    for failing, expected in [((False, False), (True, True, 0)), ((True, False), (False, True, 1)),
                              ((False, True), (True, False, 1)), ((True, True), (False, False, 1))]:  # same service twice: one warning
        deps, _ = make_test_deps(wiki_routes(*failing))
        wiki, nearby, warnings = run(fetch_wikipedia(PLACE, deps))
        assert (wiki is not None, bool(nearby), len(warnings)) == expected, failing
        assert all(w["service"] == "Wikipedia" for w in warnings)


def test_an_unexpected_exception_is_not_swallowed_as_an_upstream_failure():
    def boom(request):
        raise ValueError("bug in our code")

    deps, _ = make_test_deps({"en.wikipedia.org": boom})
    with pytest.raises(ValueError):
        run(fetch_wikipedia(PLACE, deps))


# ---- generate_answer

def test_answer_is_generated_from_the_context():
    deps, calls = make_test_deps()
    answer, warnings = run(generate_answer({"Open-Meteo": {}}, deps))
    assert "Lisbon" in answer and warnings == [] and hosts(calls) == ["llm.test"]


def test_over_the_global_budget_no_llm_call_is_made():
    deps, calls = make_test_deps(llm_daily_cap=1)
    assert run(generate_answer({}, deps))[0]
    answer, warnings = run(generate_answer({}, deps))
    assert answer is None and warnings == [BUDGET_WARNING] and len(calls) == 1


def test_llm_failure_is_a_warning_not_an_error():
    def timeout(request):
        raise httpx.ReadTimeout("cold", request=request)

    deps, _ = make_test_deps({"llm.test": timeout})
    answer, warnings = run(generate_answer({}, deps))
    assert answer is None and (warnings[0]["service"], warnings[0]["kind"]) == ("LLM", "timeout")


# ---- build_briefing wiring

def test_build_briefing_assembles_every_part_in_order():
    from ..conftest import SUMMARY
    san_diego = {**SUMMARY, "title": "San Diego", "coordinates": {"lat": 32.72, "lon": -117.16}}  # the article must sit near the place
    deps, calls = make_test_deps({GEOCODE: lambda r: json_response(GEO_SD), **wiki_routes(summary=san_diego)})
    result = run(build_briefing("San Diego", None, deps))
    assert result["city"]["region"] == "California"
    assert [a["region"] for a in result["alternatives"]] == ["Texas", "Cesar Department"]
    assert result["answer"] and result["nearby"] and result["wikipedia"] and result["warnings"] == []
    assert hosts(calls)[0] == GEOCODE and hosts(calls)[-1] == "llm.test"  # the LLM sees everything else first


def test_warnings_are_ordered_forecast_then_wikipedia_then_llm():
    deps, _ = make_test_deps({**wiki_routes(summary_fails=True), "llm.test": lambda r: json_response({"choices": []})})
    result = run(build_briefing("Lisbon", None, deps))
    assert [w["service"] for w in result["warnings"]] == ["Wikipedia", "LLM"]
