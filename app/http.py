import asyncio

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt

from .errors import UpstreamError

RETRYABLE_STATUS = {429, 502, 503, 504}
MAX_RETRY_WAIT_S = 2.0  # never hold a user request hostage to a long Retry-After
DEFAULT_RETRY_WAIT_S = 0.5
CONNECT_RETRY_WAIT_S = 0.2


async def _sleep(seconds):
    """The one place we sleep between retries (tests replace it to run instantly and to see the waits)."""
    await asyncio.sleep(seconds)


class _RetryLater(Exception):
    """Internal signal from _attempt: this failure is worth another try after `wait_s`."""

    def __init__(self, error, wait_s):
        super().__init__(str(error))
        self.error = error
        self.wait_s = wait_s


def _retry_after(res: httpx.Response):
    try:
        n = float(res.headers.get("retry-after", ""))
    except ValueError:
        return None
    return n if n >= 0 else None


def _status_error(service, res):
    kind = "rate_limited" if res.status_code == 429 else "unavailable" if res.status_code >= 500 else "bad_response"
    return UpstreamError(service, kind, status=res.status_code, retry_after_s=_retry_after(res))


async def _attempt(client, service, url, *, method, headers, json, timeout):
    """One request. Returns parsed JSON, raises UpstreamError for a failure that is final, or _RetryLater
    for one that may succeed on a second try. Timeouts are final (they are already slow)."""
    try:
        res = await client.request(method, url, headers=headers, json=json, timeout=timeout)
    except httpx.TimeoutException:
        raise UpstreamError(service, "timeout") from None
    except httpx.HTTPError:
        raise _RetryLater(UpstreamError(service, "unavailable"), CONNECT_RETRY_WAIT_S) from None

    if res.is_success:
        try:
            return res.json()
        except ValueError:
            raise UpstreamError(service, "bad_response", status=res.status_code) from None

    error = _status_error(service, res)
    wait = DEFAULT_RETRY_WAIT_S if error.retry_after_s is None else error.retry_after_s
    if res.status_code in RETRYABLE_STATUS and wait <= MAX_RETRY_WAIT_S:
        raise _RetryLater(error, wait)
    raise error


async def fetch_json(client, service, url, *, method="GET", headers=None, json=None, timeout=10.0, retries=1):
    """HTTP + JSON with a per-call timeout and a bounded retry, with every failure normalised to UpstreamError.
    Retries (tenacity) apply to connection errors and to 429/502/503/504; a Retry-After longer than
    MAX_RETRY_WAIT_S is reported instead of waited for."""
    # Every upstream here is a JSON API. Wikimedia in particular answers 403 to a generic "Accept: */*".
    headers = {"Accept": "application/json", **(headers or {})}
    retrying = AsyncRetrying(
        retry=retry_if_exception_type(_RetryLater),
        stop=stop_after_attempt(retries + 1),
        wait=lambda state: state.outcome.exception().wait_s,
        sleep=lambda seconds: _sleep(seconds),
        reraise=True,
    )
    try:
        return await retrying(_attempt, client, service, url, method=method, headers=headers, json=json, timeout=timeout)
    except _RetryLater as last:
        raise last.error from None


async def stream_lines(client, service, url, *, headers=None, json=None, timeout=10.0):
    """POST and yield the reply line by line, for streamed (server-sent event) answers. Failures become
    UpstreamError as in fetch_json, but nothing is retried: once text is flowing, a retry would repeat it.
    `timeout` limits each wait for data, so a slow first token times out but a steady stream never does."""
    headers = {"Accept": "text/event-stream", **(headers or {})}
    try:
        async with client.stream("POST", url, headers=headers, json=json, timeout=timeout) as res:
            if not res.is_success:
                raise _status_error(service, res)
            async for line in res.aiter_lines():
                yield line
    except httpx.TimeoutException:
        raise UpstreamError(service, "timeout") from None
    except httpx.HTTPError:
        raise UpstreamError(service, "unavailable") from None
