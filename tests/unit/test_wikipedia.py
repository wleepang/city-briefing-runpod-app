from urllib.parse import unquote

from app.wikipedia import NEARBY_KEEP, filter_sights, get_nearby, get_wikipedia, nearby_url

from ..conftest import SEARCH, SUMMARY, json_response, make_test_deps, run

PLACE = {"id": 1, "name": "Lisbon", "region": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}


def page(title, description, index=0):
    return {"title": title, "description": description, "index": index}


# Descriptions are real ones returned by Wikipedia's geosearch for Lisbon and San Diego.
KEPT = [
    "Historic castle in Lisbon, Portugal", "Park in San Diego, California, United States", "Urban park in San Diego, California",
    "Synagogue in Lisbon, Portugal", "Boulevard in Lisbon, Portugal", "Palace in Lisbon", "Residence/Museum in Lisbon, Portugal",
    "Fountain and sculpture in San Diego, California, U.S.", "Historic movie theater in San Diego, California",
    "Fortified tower in Lisbon", "Catholic church and national monument in Portugal", "Basilica in Lisbon",
]
DROPPED = [
    "Governing body of football in Portugal", "Portugal's serious crime investigation police", "Civil parish in Lisbon, Portugal",
    "Metro station in Lisbon", "Hospital in Lisbon", "1147 Second Crusade battle", "Defunct theatre in Lisbon, Portugal",
    "Former palace in Lisbon", "Statue in San Diego, California, U.S.", "Portuguese international institution",
    "Portuguese academic publisher", "Roundabout in Lisbon, Portugal",  # a real sight the allowlist misses (documented limit)
    "", None,
]


def test_sight_like_descriptions_are_kept_and_everything_else_is_dropped():
    for description in KEPT:
        assert filter_sights([page("X", description)]), f"should keep: {description!r}"
    for description in DROPPED:
        assert filter_sights([page("X", description)]) == [], f"should drop: {description!r}"


def test_sights_are_ordered_nearest_first_and_unindexed_pages_dropped():
    pages = [page("C", "Park in X", 2), page("A", "Park in X", 0), {"title": "NoIndex", "description": "Park in X"},
             page("Neg", "Park in X", -1), page("B", "Park in X", 1)]
    assert [p["title"] for p in filter_sights(pages)] == ["A", "B", "C"]


def test_at_most_NEARBY_KEEP_sights_are_returned():
    pages = [page(f"P{i}", "Park in X", i) for i in range(NEARBY_KEEP + 5)]
    assert len(filter_sights(pages)) == NEARBY_KEEP


def test_article_urls_are_built_from_titles():
    for title, url in [
        ("São Jorge Castle", "https://en.wikipedia.org/wiki/S%C3%A3o_Jorge_Castle"),
        ("Balboa Theatre (San Diego)", "https://en.wikipedia.org/wiki/Balboa_Theatre_(San_Diego)"),
        ("King's Park", "https://en.wikipedia.org/wiki/King's_Park"),
        ("A/B Park", "https://en.wikipedia.org/wiki/A%2FB_Park"),
    ]:
        assert filter_sights([page(title, "Park in X")])[0]["url"] == url


def test_nearby_url_searches_around_the_places_coordinates():
    url = nearby_url(PLACE)
    assert "ggscoord=38.7%7C-9.1" in url and "ggslimit=50" in url and "generator=geosearch" in url


def test_get_nearby_fetches_and_filters_with_our_user_agent():
    pages = {"query": {"pages": [page("Castle", "Historic castle", 0), page("Police", "Police force", 1)]}}
    deps, calls = make_test_deps({"en.wikipedia.org": lambda r: json_response(pages)})
    assert [n["title"] for n in run(get_nearby(deps.client, PLACE))] == ["Castle"]
    assert "city-briefing-demo" in calls[0].headers["user-agent"]


def test_get_nearby_with_no_pages_is_empty():
    for body in ({}, {"query": {}}, {"query": {"pages": None}}, {"query": {"pages": []}}):
        deps, _ = make_test_deps({"en.wikipedia.org": lambda r, body=body: json_response(body)})
        assert run(get_nearby(deps.client, PLACE)) == [], body


def summary(title, lat, lon, type="standard"):
    return {"type": type, "title": title, "extract": f"{title}.", "coordinates": {"lat": lat, "lon": lon}}


def wiki_routes(articles, search=()):
    """Fake Wikipedia: `articles` maps a title to its summary (anything else is a 404)."""
    def route(request):
        url = str(request.url)
        if "/search/page" in url:
            return json_response({"pages": [{"key": k} for k in search]})
        title = unquote(url.rsplit("/page/summary/", 1)[1])
        if title in articles:
            return json_response(articles[title])
        return json_response({"title": "Not found."}, status=404)
    return {"en.wikipedia.org": route}


def test_get_wikipedia_uses_the_exact_title_and_our_user_agent():
    deps, calls = make_test_deps()
    wiki = run(get_wikipedia(deps.client, PLACE))
    assert wiki == {"title": "Lisbon", "description": "Capital of Portugal", "extract": "Lisbon is the capital.",
                    "url": "https://en.wikipedia.org/wiki/Lisbon"}
    assert len(calls) == 1 and "/page/summary/Lisbon" in str(calls[0].url)
    assert all("city-briefing-demo" in c.headers["user-agent"] for c in calls)  # Wikimedia 403s anonymous clients


def test_get_wikipedia_rejects_an_article_about_somewhere_else_and_tries_the_next_title():
    # "Portland" is Maine; the geocoder found Portland, Oregon.
    place = {"name": "Portland", "region": "Oregon", "country": "United States", "latitude": 45.5, "longitude": -122.7}
    deps, _ = make_test_deps(wiki_routes({
        "Portland": summary("Portland", 43.66, -70.26, type="standard"),
        "Portland, Oregon": summary("Portland, Oregon", 45.52, -122.68),
    }))
    assert run(get_wikipedia(deps.client, place))["title"] == "Portland, Oregon"


def test_get_wikipedia_skips_disambiguation_pages_and_articles_without_coordinates():
    place = {"name": "Kyoto", "region": "Kyoto", "country": "Japan", "latitude": 35.01, "longitude": 135.77}
    deps, _ = make_test_deps(wiki_routes({
        "Kyoto": {"type": "disambiguation", "title": "Kyoto (disambiguation)", "coordinates": {"lat": 35.0, "lon": 135.7}},
        "Kyoto, Kyoto": {"type": "standard", "title": "Cosmology of Kyoto", "extract": "A 1993 video game."},
        "Kyoto, Japan": summary("Kyoto", 35.01, 135.77),
    }))
    assert run(get_wikipedia(deps.client, place))["title"] == "Kyoto"


def test_get_wikipedia_falls_back_to_search_and_still_checks_where_the_hit_is():
    deps, calls = make_test_deps(wiki_routes(
        {"Lisboa": summary("Lisbon", 38.72, -9.14), "Lisbon District": summary("Lisbon District", 39.0, -9.0)},
        search=["Lisbon District", "Lisboa"],
    ))
    # District's centre is ~35 km away, inside the 50 km limit, so the first search hit is accepted.
    assert run(get_wikipedia(deps.client, PLACE))["title"] == "Lisbon District"
    far = {"name": "Lisbon", "latitude": 20.0, "longitude": 20.0}
    assert run(get_wikipedia(deps.client, far)) is None  # nothing near the point: no wrong article


def test_get_wikipedia_with_no_match_anywhere_returns_none():
    deps, _ = make_test_deps(wiki_routes({}))
    assert run(get_wikipedia(deps.client, PLACE)) is None


def test_get_wikipedia_tolerates_a_summary_without_links_and_escapes_slashes_in_titles():
    place = {"name": "AC/DC", "latitude": 1.0, "longitude": 1.0}
    deps, calls = make_test_deps(wiki_routes({"AC/DC": summary("AC/DC", 1.0, 1.0)}))
    wiki = run(get_wikipedia(deps.client, place))
    assert wiki["url"] is None and wiki["description"] is None
    assert "/page/summary/AC%2FDC" in str(calls[0].url)
