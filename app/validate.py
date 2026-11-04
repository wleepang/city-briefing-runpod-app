import re
import unicodedata

from .errors import HttpError

MAX_CITY = 80
_ID_RE = re.compile(r"[0-9]{1,10}")  # not \d: that also matches non-ASCII digits
_EXTRA_CITY_CHARS = set(" .,'’()-")


def _city_char_ok(ch):
    return unicodedata.category(ch)[0] in ("L", "M") or ch in "0123456789" or ch in _EXTRA_CITY_CHARS


def _single(params, name):
    values = params.getlist(name)
    if len(values) > 1:
        raise HttpError(400, f'"{name}" must be given once', "bad_input")
    return values[0] if values else ""


def parse_briefing_query(params):
    """Validate and normalise query params (a Starlette QueryParams). Raises HttpError(400)."""
    id_ = _single(params, "id").strip()
    if id_ and not _ID_RE.fullmatch(id_):
        raise HttpError(400, '"id" must be a numeric place id', "bad_input")

    # "city" is the search text; "id" (optional) pins an exact place picked from the alternatives.
    city = " ".join(_single(params, "city").split())
    if not city and not id_:
        raise HttpError(400, 'Missing "city"', "bad_input")
    if len(city) > MAX_CITY:
        raise HttpError(400, f'"city" must be at most {MAX_CITY} characters', "bad_input")
    if city and not all(_city_char_ok(c) for c in city):
        raise HttpError(400, '"city" contains unsupported characters', "bad_input")
    return {"city": city, "id": id_ or None}
