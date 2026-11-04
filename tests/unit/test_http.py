"""fetch_json: our retry policy and the translation of HTTP outcomes into our error vocabulary.
(The retry loop itself is tenacity's; these tests pin what we told it to do. Waits are recorded by `sleeps`.)"""
import httpx
import pytest

from app.errors import UpstreamError
from app.http import fetch_json

from ..conftest import json_response, run


def call(handler, **kw):
    calls = []

    def recording(request):
        calls.append(request)
        return handler(request, len(calls))

    client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    kw.setdefault("timeout", 1)
    return run(fetch_json(client, "Svc", "https://svc.test/x", **kw)), calls


def fails(handler, **kw):
    calls = []
    with pytest.raises(UpstreamError) as e:
        _, calls = call(lambda r, n: calls.append(n) or handler(r, n), **kw)
    e.value.attempts = len(calls)
    return e.value


def respond(status, headers=None):
    return lambda request, n: json_response({}, status, headers)


def connection_error(request, n):
    raise httpx.ConnectError("down", request=request)


# ---- what we send

def test_a_call_sends_json_headers_merged_with_the_callers_and_an_optional_json_body():
    data, calls = call(lambda r, n: json_response({"ok": 1}))
    assert data == {"ok": 1} and calls[0].headers["accept"] == "application/json"

    _, calls = call(lambda r, n: json_response({}), headers={"User-Agent": "ua/1", "Accept": "text/x"})
    assert (calls[0].headers["user-agent"], calls[0].headers["accept"]) == ("ua/1", "text/x")

    _, calls = call(lambda r, n: json_response({}), method="POST", json={"a": 1})
    assert (calls[0].method, calls[0].content) == ("POST", b'{"a":1}')


# ---- how outcomes are classified

def test_a_2xx_that_is_not_json_is_a_bad_response():
    err = fails(lambda r, n: httpx.Response(200, text="<html>not json</html>"))
    assert (err.kind, err.status, err.attempts) == ("bad_response", 200, 1)


def test_client_errors_and_plain_500s_are_final_and_never_retried(sleeps):
    # A 500 is usually a bug on their side, not a blip: fail fast and let the stale cache cover it.
    for status, kind in [(404, "bad_response"), (400, "bad_response"), (500, "unavailable")]:
        err = fails(respond(status))
        assert (err.kind, err.status, err.attempts) == (kind, status, 1), status
    assert sleeps == []


def test_a_timeout_is_final_because_it_is_already_slow(sleeps):
    def timeout(request, n):
        raise httpx.ReadTimeout("slow", request=request)

    err = fails(timeout)
    assert (err.kind, err.attempts, sleeps) == ("timeout", 1, [])


# ---- retry policy

def test_throttling_and_gateway_errors_are_retried_once_after_the_default_wait_then_reported(sleeps):
    for status, kind in [(429, "rate_limited"), (502, "unavailable"), (503, "unavailable"), (504, "unavailable")]:
        sleeps.clear()
        err = fails(respond(status))
        assert (err.kind, err.attempts, sleeps) == (kind, 2, [0.5]), status


def test_a_transient_failure_then_success_returns_the_data(sleeps):
    data, calls = call(lambda r, n: json_response({}, 503) if n == 1 else json_response({"ok": True}))
    assert data == {"ok": True} and len(calls) == 2 and sleeps == [0.5]


def test_retry_after_sets_the_wait_and_garbage_falls_back_to_the_default(sleeps):
    for header, waited in [("1.5", 1.5), ("0", 0.0), ("soon", 0.5)]:
        sleeps.clear()
        data, _ = call(lambda r, n, h=header: json_response({}, 429, {"retry-after": h}) if n == 1 else json_response({"ok": 1}))
        assert (data, sleeps) == ({"ok": 1}, [waited]), header


def test_a_long_retry_after_is_reported_not_waited_for(sleeps):
    # Waiting 30s inside a user request would be worse than failing fast.
    err = fails(respond(429, {"retry-after": "30"}))
    assert (err.kind, err.retry_after_s, err.attempts, sleeps) == ("rate_limited", 30.0, 1, [])


def test_connection_errors_are_retried_once_after_a_short_wait(sleeps):
    err = fails(connection_error)
    assert (err.kind, err.attempts, sleeps) == ("unavailable", 2, [0.2])

    sleeps.clear()
    data, calls = call(lambda r, n: connection_error(r, n) if n == 1 else json_response({"ok": 1}))
    assert data == {"ok": 1} and len(calls) == 2 and sleeps == [0.2]


def test_retries_can_be_disabled(sleeps):
    err = fails(respond(503), retries=0)
    assert (err.attempts, sleeps) == (1, [])
