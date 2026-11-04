"""End to end through FastAPI with mocked upstreams. These check the WIRING (routes, validation, error mapping,
rate limiting, response shape). The rules behind them are tested in tests/unit."""
import re

import httpx
from fastapi.testclient import TestClient

from app.llm import REQUEST
from app.main import create_app

from ..conftest import SAN_DIEGO, config_for_tests, json_response, sse_response

GEOCODE = "geocoding-api.open-meteo.com"


def test_a_briefing_has_the_documented_shape_and_leaks_no_secrets(make_app):
    app = make_app()
    res = app.get("/api/briefing", params={"city": "Lisbon"})
    body = res.json()
    assert res.status_code == 200
    assert set(body) == {"city", "weather", "wikipedia", "nearby", "answer", "alternatives", "warnings"}
    assert body["city"] == {"id": 2267057, "name": "Lisbon", "region": "Lisbon", "country": "Portugal"}
    assert body["answer"] and body["warnings"] == [] and body["nearby"][0]["url"].startswith("https://en.wikipedia.org/")
    assert body["weather"]["source_url"].startswith("https://api.open-meteo.com/")
    assert "secret-key" not in res.text
    assert app.llm_messages()[1]["content"].endswith(REQUEST)


def test_a_required_upstream_failing_is_mapped_to_a_clear_error(make_app):
    app = make_app({GEOCODE: lambda r: json_response({}, 429, {"retry-after": "30"})})
    res = app.get("/api/briefing?city=Lisbon")
    assert res.status_code == 429 and res.headers["retry-after"] == "30"
    assert res.json() == {"error": "Open-Meteo is rate-limiting us right now. Please try again shortly.",
                          "code": "rate_limited", "service": "Open-Meteo"}
    assert app.calls_to("llm.test") == []


def test_an_ambiguous_city_can_be_corrected_by_picking_an_alternative(make_app):
    def geocode(request):
        return json_response(SAN_DIEGO[1] if "/v1/get" in str(request.url) else {"results": SAN_DIEGO})

    app = make_app({GEOCODE: geocode})
    first = app.get("/api/briefing?city=San Diego").json()
    assert first["city"]["region"] == "California"
    pick = first["alternatives"][0]
    second = app.get(f"/api/briefing?city=San Diego&id={pick['id']}").json()
    assert second["city"]["region"] == "Texas" == pick["region"]
    assert "California" in [a["region"] for a in second["alternatives"]]


def test_bad_input_and_unknown_places_are_client_errors_that_reach_no_upstream(make_app):
    app = make_app({GEOCODE: lambda r: json_response({})})
    assert app.get("/api/briefing?city=%3Cscript%3E").status_code == 400
    assert app.get("/api/briefing").status_code == 400
    assert app.calls == []
    res = app.get("/api/briefing?city=Nowhereville")
    assert (res.status_code, res.json()["code"]) == (404, "city_not_found")



def test_an_unexpected_bug_is_a_generic_500_that_leaks_nothing(make_app, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("db password is hunter2")

    monkeypatch.setattr("app.main.build_briefing", boom)
    app = make_app()
    res = TestClient(app.app, raise_server_exceptions=False).get("/api/briefing?city=Lisbon")
    assert res.status_code == 500 and res.json() == {"error": "Internal server error", "code": "internal"}
    assert "hunter2" not in res.text


def test_health_and_the_frontend_are_served(make_app):
    app = make_app()
    assert app.get("/health").json() == {"ok": True, "model": "m"}
    page = app.get("/")
    assert page.status_code == 200 and "City Briefing" in page.text


def test_an_app_that_creates_its_own_http_client_closes_it_on_shutdown():
    app = create_app(config_for_tests())
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert not app.state.deps.client.is_closed
    assert app.state.deps.client.is_closed


def test_an_upstream_server_error_is_a_graceful_502_that_hides_the_upstream_body(make_app):
    leaky = {"traceback": "File app.py line 3, password=hunter2"}
    for status in (500, 502, 503, 504):
        app = make_app({GEOCODE: lambda r, status=status: json_response(leaky, status)})
        res = app.get("/api/briefing?city=Lisbon")
        assert res.status_code == 502, status
        assert res.json() == {"error": "Open-Meteo is currently unavailable.", "code": "unavailable", "service": "Open-Meteo"}
        assert "hunter2" not in res.text
        assert app.calls_to("llm.test") == []  # nothing downstream is attempted without a place


def test_an_upstream_500_from_an_optional_service_is_only_a_warning(make_app):
    app = make_app({"en.wikipedia.org": lambda r: json_response({}, 500), "llm.test": lambda r: json_response({}, 500)})
    res = app.get("/api/briefing?city=Lisbon")
    assert res.status_code == 200
    assert [w["kind"] for w in res.json()["warnings"]] == ["unavailable", "unavailable"]


def test_the_stream_route_sends_the_data_first_then_the_answer_as_server_sent_events(make_app):
    app = make_app({"llm.test": lambda r: sse_response("Nice ", "day.")})
    res = app.get("/api/briefing/stream", params={"city": "Lisbon"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache" and res.headers["x-accel-buffering"] == "no"
    assert re.findall(r"^event: (\w+)$", res.text, re.M) == ["briefing", "delta", "delta", "done"]
    assert "secret-key" not in res.text


def test_the_stream_route_reports_problems_found_before_the_first_event_as_normal_errors(make_app):
    assert make_app().get("/api/briefing/stream?city=").status_code == 400
    res = make_app({GEOCODE: lambda r: json_response({}, 429, {"retry-after": "30"})}).get("/api/briefing/stream?city=Lisbon")
    assert res.status_code == 429 and res.headers["content-type"].startswith("application/json")


def test_static_files_must_be_revalidated_so_a_new_page_never_runs_against_an_old_script(make_app):
    res = make_app().get("/render.js")
    assert res.status_code == 200 and res.headers["cache-control"] == "no-cache" and "etag" in res.headers
    assert make_app().get("/render.js", headers={"if-none-match": res.headers["etag"]}).status_code == 304
