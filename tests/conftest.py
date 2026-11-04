"""Mock the upstream APIs by hostname via httpx.MockTransport; the app gets the mocked client injected."""
import asyncio
import json as jsonlib
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import http as app_http
from app.config import Config, LLMConfig
from app.briefing import make_deps
from app.main import create_app

LISBON = {"id": 2267057, "name": "Lisbon", "admin1": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}
GEO = {"results": [LISBON]}
FORECAST = {
    "current": {"temperature_2m": 70, "apparent_temperature": 69, "relative_humidity_2m": 55, "precipitation": 0,
                "weather_code": 1, "wind_speed_10m": 8},
    "daily": {
        "time": ["2026-01-01", "2026-01-02", "2026-01-03"],
        "weather_code": [1, 3, 61],
        "temperature_2m_max": [72, 70, 66],
        "temperature_2m_min": [58, 57, 55],
        "precipitation_probability_max": [5, 20, 70],
        "sunrise": ["07:00", "07:00", "07:01"],
        "sunset": ["18:00", "18:01", "18:02"],
    },
}
SEARCH = {"pages": [{"key": "Lisbon"}]}
SUMMARY = {"type": "standard", "title": "Lisbon", "description": "Capital of Portugal", "extract": "Lisbon is the capital.",
           "coordinates": {"lat": 38.72, "lon": -9.14},
           "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Lisbon"}}}
NEARBY = {"query": {"pages": [
    {"pageid": 1, "title": "Belém Tower", "description": "Fortified tower in Lisbon", "index": 2},
    {"pageid": 2, "title": "São Jorge Castle", "description": "Historic castle in Lisbon, Portugal", "index": 0},
    {"pageid": 3, "title": "Santa Justa, Lisbon", "description": "Civil parish in Lisbon, Portugal", "index": 1},
    {"pageid": 4, "title": "Martim Moniz Station", "description": "Metro station in Lisbon", "index": 3},
    {"pageid": 5, "title": "Siege of Lisbon", "description": "1147 battle", "index": -1},
    {"pageid": 6, "title": "Carmo Convent", "description": "Ruined convent in Lisbon", "index": 4},
    {"pageid": 7, "title": "Polícia Judiciária", "description": "Portugal's serious crime investigation police", "index": 5},
    {"pageid": 8, "title": "Military Academy", "index": 6},
    {"pageid": 9, "title": "Teatro Avenida", "description": "Defunct theatre in Lisbon", "index": 7},
]}}
LLM_OK = {"choices": [{"message": {"content": "Nice day in Lisbon [Open-Meteo]. Capital city [Wikipedia]."}}]}

SAN_DIEGO = [
    {"id": 5391811, "name": "San Diego", "admin1": "California", "country": "United States", "latitude": 32.7, "longitude": -117.2},
    {"id": 4726311, "name": "San Diego", "admin1": "Texas", "country": "United States", "latitude": 27.8, "longitude": -98.2},
    {"id": 3669947, "name": "San Diego", "admin1": "Cesar Department", "country": "Colombia", "latitude": 10.3, "longitude": -73.2},
]


def json_response(body, status=200, headers=None):
    return httpx.Response(status, json=body, headers=headers)


def sse_response(*pieces, extra=""):
    """A streamed model reply: one chunk per piece, then [DONE] (what vLLM and LM Studio send)."""
    chunks = "".join(f"data: {jsonlib.dumps({'choices': [{'delta': {'content': p}}]})}\n\n" for p in pieces)
    return httpx.Response(200, content=(chunks + extra + "data: [DONE]\n\n").encode(), headers={"content-type": "text/event-stream"})


def run(coro):
    """Run a coroutine from a plain sync test (no pytest-asyncio needed)."""
    return asyncio.run(coro)


def _wikipedia(request):
    url = str(request.url)
    if "/search/page" in url:
        return json_response(SEARCH)
    return json_response(NEARBY if "/w/api.php" in url else SUMMARY)


def default_routes():
    """Happy-path upstreams keyed by hostname; override a host per test."""
    return {
        "geocoding-api.open-meteo.com": lambda r: json_response(GEO),
        "api.open-meteo.com": lambda r: json_response(FORECAST),
        "en.wikipedia.org": _wikipedia,
        "llm.test": lambda r: json_response(LLM_OK),
    }


def mock_client(overrides=None):
    """An httpx client whose upstreams are the routes above. Returns (client, calls); `calls` records requests."""
    calls = []
    routes = {**default_routes(), **(overrides or {})}

    def handler(request: httpx.Request):
        calls.append(request)
        route = routes.get(request.url.host)
        if route is None:
            raise AssertionError(f"unmocked host {request.url.host}")
        return route(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), calls


def config_for_tests(llm=None, **overrides):
    return replace(
        Config(llm=LLMConfig(base_url="http://llm.test/v1", api_key="secret-key", model="m", **(llm or {}))),
        **overrides,
    )


def make_test_deps(overrides=None, llm=None, **config_overrides):
    """Deps wired to mocked upstreams, for testing workflow pieces without FastAPI. Returns (deps, calls)."""
    client, calls = mock_client(overrides)
    return make_deps(config_for_tests(llm, **config_overrides), client), calls


class Harness:
    """The whole app behind FastAPI's TestClient with mocked upstreams (for the few end-to-end tests)."""

    def __init__(self, overrides=None, llm=None, **config_overrides):
        client, self.calls = mock_client(overrides)
        self.app = create_app(config_for_tests(llm, **config_overrides), client=client)
        self.client = TestClient(self.app)

    def get(self, path, **kw):
        return self.client.get(path, **kw)

    def calls_to(self, host):
        return [c for c in self.calls if c.url.host == host]

    def llm_messages(self, index=-1):
        return jsonlib.loads(self.calls_to("llm.test")[index].content)["messages"]


@pytest.fixture
def make_app():
    return Harness


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Retries never really sleep in tests; the waits they asked for are recorded here."""
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(app_http, "_sleep", fake_sleep)
    return waits
