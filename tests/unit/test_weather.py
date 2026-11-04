import pytest

from app.errors import HttpError, UpstreamError
from app.weather import forecast_url, get_place_by_id, search_places, shape_forecast, to_place

from ..conftest import FORECAST, GEO, LISBON, json_response, make_test_deps, run

PLACE = {"id": 1, "name": "Lisbon", "region": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1}


def test_to_place_renames_admin1_and_tolerates_missing_fields():
    assert to_place(LISBON)["region"] == "Lisbon"
    assert to_place({"id": 5, "name": "X"}) == {
        "id": 5, "name": "X", "region": None, "country": None, "latitude": None, "longitude": None}


def test_forecast_url_uses_the_places_coordinates_and_imperial_units():
    url = forecast_url(PLACE)
    for fragment in ("latitude=38.7", "longitude=-9.1", "forecast_days=3", "temperature_unit=fahrenheit",
                     "wind_speed_unit=mph", "precipitation_unit=inch"):
        assert fragment in url
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")


def test_shape_forecast_maps_values_and_weather_codes():
    wx = shape_forecast(PLACE, FORECAST, "https://src")
    assert wx["source_url"] == "https://src"
    assert wx["place"] == {"id": 1, "name": "Lisbon", "region": "Lisbon", "country": "Portugal"}  # no coordinates
    assert wx["current"]["temp_f"] == 70 and wx["current"]["conditions"] == "mainly clear"
    assert [d["conditions"] for d in wx["forecast"]] == ["mainly clear", "overcast", "light rain"]
    assert wx["forecast"][2]["precip_chance_pct"] == 70


def test_unknown_weather_code_is_shown_not_crashed_on():
    raw = {**FORECAST, "current": {**FORECAST["current"], "weather_code": 7}}
    assert shape_forecast(PLACE, raw, "u")["current"]["conditions"] == "code 7"


def test_unexpected_forecast_shapes_are_bad_responses():
    for raw in [
        {}, {"current": FORECAST["current"]}, {"daily": FORECAST["daily"]},
        {"current": FORECAST["current"], "daily": {"time": "2026-01-01"}},
        {"current": {"temperature_2m": 1}, "daily": FORECAST["daily"]},  # current is missing keys
        {"current": FORECAST["current"], "daily": {**FORECAST["daily"], "temperature_2m_max": [1]}},  # arrays too short
        [],  # valid JSON, but not an object
        {"current": "oops", "daily": "oops"},
    ]:
        with pytest.raises(UpstreamError) as e:
            shape_forecast(PLACE, raw, "u")
        assert e.value.kind == "bad_response", raw


def test_search_places_asks_for_several_matches_and_returns_them_in_order():
    deps, calls = make_test_deps({"geocoding-api.open-meteo.com": lambda r: json_response({"results": [LISBON, {**LISBON, "id": 2}]})})
    places = run(search_places(deps.client, "Lisbon"))
    assert [p["id"] for p in places] == [LISBON["id"], 2]
    assert "count=5" in str(calls[0].url)


def test_search_places_with_no_results_is_an_empty_list():
    for body in ({}, {"results": None}, {"results": []}):
        deps, _ = make_test_deps({"geocoding-api.open-meteo.com": lambda r, body=body: json_response(body)})
        assert run(search_places(deps.client, "Nowhere")) == [], body


def test_get_place_by_id_returns_the_place():
    deps, calls = make_test_deps({"geocoding-api.open-meteo.com": lambda r: json_response(LISBON)})
    assert run(get_place_by_id(deps.client, "2267057"))["name"] == "Lisbon"
    assert "id=2267057" in str(calls[0].url)


def test_unknown_id_becomes_a_404_but_other_failures_stay_upstream_errors():
    deps, _ = make_test_deps({"geocoding-api.open-meteo.com": lambda r: json_response({"error": True}, 400)})
    with pytest.raises(HttpError) as e:
        run(get_place_by_id(deps.client, "1"))
    assert (e.value.status, e.value.code) == (404, "city_not_found")

    deps, _ = make_test_deps({"geocoding-api.open-meteo.com": lambda r: json_response({}, 500)})
    with pytest.raises(UpstreamError) as e:
        run(get_place_by_id(deps.client, "1"))
    assert e.value.kind == "unavailable"
