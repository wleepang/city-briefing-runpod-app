from urllib.parse import urlencode

from .errors import HttpError, UpstreamError
from .http import fetch_json

SERVICE = "Open-Meteo"
GEO = "https://geocoding-api.open-meteo.com/v1"
MAX_MATCHES = 5

# WMO weather codes -> text (used by Open-Meteo)
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "heavy rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


def _describe(code):
    return WMO.get(code, f"code {code}")


def to_place(r):
    return {
        "id": r.get("id"), "name": r.get("name"), "region": r.get("admin1"), "country": r.get("country"),
        "latitude": r.get("latitude"), "longitude": r.get("longitude"),
    }


def _call(client, url):
    return fetch_json(client, SERVICE, url)


async def search_places(client, city):
    """Geocode a name. Many names are ambiguous ("San Diego"), so return several matches, best first."""
    q = urlencode({"name": city, "count": MAX_MATCHES, "language": "en", "format": "json"})
    geo = await _call(client, f"{GEO}/search?{q}")
    return [to_place(r) for r in geo.get("results") or []]


async def get_place_by_id(client, place_id):
    """Look a place up by its Open-Meteo id (used when the user picks one of the alternatives)."""
    try:
        return to_place(await _call(client, f"{GEO}/get?{urlencode({'id': place_id})}"))
    except UpstreamError as err:
        if err.status == 400:
            raise HttpError(404, "Unknown place id", "city_not_found") from None
        raise


def forecast_url(place):
    params = urlencode({
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
        "forecast_days": "3",
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    })
    return f"https://api.open-meteo.com/v1/forecast?{params}"


def shape_forecast(place, raw, source_url):
    """Turn Open-Meteo's raw forecast JSON into our response shape. Raises UpstreamError(bad_response)
    if the payload isn't the shape we expect."""
    try:
        c, d = raw.get("current"), raw.get("daily")
        if not c or not d or not isinstance(d.get("time"), list):
            raise KeyError("current/daily")
        return {
            "source_url": source_url,  # shown as a link in the UI; stripped from the LLM context
            "place": {k: place[k] for k in ("id", "name", "region", "country")},
            "current": {
                "temp_f": c["temperature_2m"],
                "feels_like_f": c["apparent_temperature"],
                "humidity_pct": c["relative_humidity_2m"],
                "wind_mph": c["wind_speed_10m"],
                "precip_in": c["precipitation"],
                "conditions": _describe(c["weather_code"]),
            },
            "forecast": [
                {
                    "date": date,
                    "high_f": d["temperature_2m_max"][i],
                    "low_f": d["temperature_2m_min"][i],
                    "precip_chance_pct": d["precipitation_probability_max"][i],
                    "conditions": _describe(d["weather_code"][i]),
                    "sunrise": d["sunrise"][i],
                    "sunset": d["sunset"][i],
                }
                for i, date in enumerate(d["time"])
            ],
        }
    except (KeyError, IndexError, TypeError, AttributeError):  # AttributeError: raw (or a section) isn't an object
        raise UpstreamError(SERVICE, "bad_response") from None


async def get_forecast(client, place):
    """Current conditions + 3-day forecast (imperial units) for a resolved place."""
    url = forecast_url(place)
    return shape_forecast(place, await _call(client, url), url)
