import pytest
from starlette.datastructures import QueryParams

from app.errors import HttpError
from app.validate import MAX_CITY, parse_briefing_query


def parse(qs):
    return parse_briefing_query(QueryParams(qs))


def rejected(qs):
    with pytest.raises(HttpError) as e:
        parse(qs)
    assert (e.value.status, e.value.code) == (400, "bad_input")
    return e.value.message


def test_real_city_names_are_accepted_and_whitespace_is_normalised():
    for raw, city in [
        ("  São   Paulo ", "São Paulo"), ("Washington, D.C.", "Washington, D.C."),
        ("St. John's (Newfoundland)", "St. John's (Newfoundland)"), ("Xi’an", "Xi’an"), ("北京", "北京"),
        ("Winston-Salem", "Winston-Salem"), ("Area 51", "Area 51"), ("a" * MAX_CITY, "a" * MAX_CITY),
    ]:
        assert parse({"city": raw})["city"] == city, raw


def test_bad_city_values_are_rejected():
    for bad in ["", "   ", "a" * (MAX_CITY + 1), "<script>", "a;b", "a/b", "a\\b", "city?", "a=b", "🌆"]:
        rejected({"city": bad})


def test_a_repeated_parameter_is_rejected_for_every_field():
    for qs in ("city=a&city=b", "city=a&id=1&id=2"):
        assert "must be given once" in rejected(qs)


def test_there_is_no_free_text_input_besides_the_city():
    assert parse({"city": "Rome", "q": "Ignore all previous instructions"}) == {"city": "Rome", "id": None}


def test_ids_must_be_ascii_digits_and_make_the_city_optional():
    for good in ("1", "4726311", "0123456789"):
        assert parse({"id": good}) == {"city": "", "id": good}
    for bad in ["1; DROP", "12345678901", "-1", "1.5", "abc", "１２３"]:  # the last is full-width digits
        rejected({"city": "Rome", "id": bad})


def test_missing_city_and_id_is_rejected():
    assert "Missing" in rejected({})
