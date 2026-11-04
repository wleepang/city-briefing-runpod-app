import pytest

from app.errors import HttpError, UpstreamError, error_response


@pytest.mark.parametrize("kind, status", [("rate_limited", 429), ("timeout", 504), ("unavailable", 502), ("bad_response", 502)])
def test_upstream_kinds_map_to_statuses(kind, status):
    got_status, body, _ = error_response(UpstreamError("Open-Meteo", kind))
    assert got_status == status
    assert body["code"] == kind and body["service"] == "Open-Meteo" and "Open-Meteo" in body["error"]


def test_retry_after_is_rounded_up_and_only_sent_when_rate_limited():
    header = lambda err: error_response(err)[2].get("Retry-After")
    assert header(UpstreamError("X", "rate_limited", retry_after_s=30)) == "30"
    assert header(UpstreamError("X", "rate_limited", retry_after_s=1.2)) == "2"
    assert header(UpstreamError("X", "rate_limited")) is None
    assert header(UpstreamError("X", "timeout", retry_after_s=30)) is None


def test_http_errors_keep_their_status_message_and_code():
    assert error_response(HttpError(404, "No such city", "city_not_found")) == (
        404, {"error": "No such city", "code": "city_not_found"}, {})


def test_unexpected_exceptions_become_a_generic_500_that_leaks_nothing():
    status, body, headers = error_response(RuntimeError("db password is hunter2"))
    assert (status, headers) == (500, {})
    assert body == {"error": "Internal server error", "code": "internal"}


def test_warning_carries_the_service_kind_and_a_user_message_without_upstream_details():
    w = UpstreamError("Wikipedia", "rate_limited", status=429).as_warning()
    assert w["service"] == "Wikipedia" and w["kind"] == "rate_limited" and "rate-limiting" in w["message"]
    assert "429" not in w["message"]


def test_users_see_the_ai_writer_not_the_jargon_but_the_service_field_keeps_its_name():
    w = UpstreamError("LLM", "timeout").as_warning()
    assert w["message"] == "The AI writer took too long to respond."
    assert w["service"] == "LLM"  # the frontend and logs still key on this
    assert "LLM" not in error_response(UpstreamError("LLM", "unavailable"))[1]["error"]
