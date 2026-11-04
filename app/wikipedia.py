import re
from math import asin, cos, radians, sin, sqrt
from urllib.parse import quote, urlencode

from .errors import UpstreamError
from .http import fetch_json

SERVICE = "Wikipedia"
# Wikimedia asks API clients to send a descriptive User-Agent.
HEADERS = {"User-Agent": "city-briefing-demo/1.0"}


MAX_ARTICLE_DISTANCE_KM = 50
SEARCH_FALLBACK_HITS = 3


def km_apart(lat1, lon1, lat2, lon2):
    """Great-circle distance (haversine)."""
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(a))


def is_this_place(summary, place):
    """A real article about a geographic place, located near where the geocoder put it. Search alone is
    fuzzy: its top hit for "Kyoto" was a video game, and for "Nairobi" a terror attack."""
    where = summary.get("coordinates") or {}
    if summary.get("type") != "standard" or "lat" not in where or "lon" not in where:
        return False
    return km_apart(place["latitude"], place["longitude"], where["lat"], where["lon"]) <= MAX_ARTICLE_DISTANCE_KM


def shape_summary(summary):
    return {
        "title": summary.get("title"),
        "description": summary.get("description"),
        "extract": summary.get("extract"),
        "url": ((summary.get("content_urls") or {}).get("desktop") or {}).get("page"),
    }


async def fetch_summary(client, key):
    """The article summary, or None when no article has that title."""
    try:
        return await fetch_json(
            client, SERVICE, f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(key, safe='')}",
            headers=HEADERS,
        )
    except UpstreamError as err:
        if err.status == 404:
            return None
        raise


async def get_wikipedia(client, place):
    """Find the article about THIS place and return its summary, or None rather than a wrong article.
    Exact titles first ("Portland, Oregon" for an ambiguous name), then search as a fallback; each
    candidate must pass is_this_place."""
    name, region, country = place.get("name"), place.get("region"), place.get("country")
    titles = list(dict.fromkeys(t for t in (name, region and f"{name}, {region}", country and f"{name}, {country}") if t))
    for title in titles:
        summary = await fetch_summary(client, title)
        if summary and is_this_place(summary, place):
            return shape_summary(summary)
    q = " ".join(str(p) for p in (name, region, country) if p)
    search = await fetch_json(
        client, SERVICE,
        f"https://en.wikipedia.org/w/rest.php/v1/search/page?{urlencode({'q': q, 'limit': SEARCH_FALLBACK_HITS})}",
        headers=HEADERS,
    )
    for hit in (search.get("pages") or [])[:SEARCH_FALLBACK_HITS]:
        if hit["key"] in titles:
            continue
        summary = await fetch_summary(client, hit["key"])
        if summary and is_this_place(summary, place):
            return shape_summary(summary)
    return None


NEARBY_RADIUS_M = 5000
NEARBY_FETCH = 50
NEARBY_KEEP = 8
# Geosearch returns anything with coordinates (police bodies, federations, hospitals...). Keep only
# articles whose short description sounds like somewhere a visitor would go. Heuristic, not exhaustive.
_SIGHT_WORDS = (
    "castle", "fort", "fortress", "tower", "palace", "park", "garden", "museum", "gallery", "church", "cathedral",
    "basilica", "monastery", "convent", "mosque", "synagogue", "temple", "square", "plaza", "boulevard", "bridge",
    "monument", "memorial", "fountain", "theatre", "theater", "opera", "market", "viewpoint", "lookout",
    "beach", "aqueduct", "lighthouse", "observatory", "zoo", "aquarium", "bullring", "stadium", "pier", "waterfront",
    "promenade", "cemetery", "ruins", "plaza", "arena", "elevator", "funicular",
)
_GONE_RE = re.compile(r"\b(defunct|former|demolished|destroyed|closed)\b", re.IGNORECASE)
_SIGHT_RE = re.compile(r"\b(" + "|".join(_SIGHT_WORDS) + r")s?\b", re.IGNORECASE)


def nearby_url(place):
    q = urlencode({
        "action": "query", "generator": "geosearch",
        "ggscoord": f"{place['latitude']}|{place['longitude']}",
        "ggsradius": NEARBY_RADIUS_M, "ggslimit": NEARBY_FETCH,
        "prop": "description", "format": "json", "formatversion": 2,
    })
    return f"https://en.wikipedia.org/w/api.php?{q}"


def filter_sights(pages):
    """Geosearch pages -> nearest-first list of things worth visiting (see _SIGHT_WORDS)."""
    out = []
    for p in sorted((p for p in pages if p.get("index", -1) >= 0), key=lambda p: p["index"]):
        desc = p.get("description") or ""
        if not _SIGHT_RE.search(desc) or _GONE_RE.search(desc):
            continue
        title = p["title"]
        out.append({
            "title": title, "description": desc or None,
            "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"), safe="_(),'"),
        })
    return out[:NEARBY_KEEP]


async def get_nearby(client, place):
    """Wikipedia articles near the place's coordinates (from the Open-Meteo geocoder), nearest first.
    This is the chained step: one API's output (lat/lon) is the other API's input."""
    data = await fetch_json(client, SERVICE, nearby_url(place), headers=HEADERS)
    return filter_sights((data.get("query") or {}).get("pages") or [])
