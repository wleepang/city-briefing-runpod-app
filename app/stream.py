"""Server-sent events for the streaming briefing: event formatting and keepalives while the model is slow."""
import asyncio
import json
import logging

from .briefing import stream_answer
from .errors import UpstreamError

log = logging.getLogger("city-briefing")
# Runpod's Pod proxy closes a connection that stays silent for ~100s. A cold start is silent for 80-90s,
# so a comment line every 15s keeps it open.
KEEPALIVE_S = 15.0
_END = object()


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def with_keepalive(events, interval_s):
    """Pass through an async iterator's items, yielding None each time `interval_s` passes without one."""
    it = events.__aiter__()

    async def pull():
        try:
            return await it.__anext__()
        except StopAsyncIteration:
            return _END

    pending = asyncio.ensure_future(pull())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval_s)
            if not done:
                yield None
                continue
            item = pending.result()
            if item is _END:
                return
            yield item
            pending = asyncio.ensure_future(pull())
    finally:  # the browser went away: stop waiting on the model
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)  # let the cancel land before closing the generator
        await events.aclose()


async def briefing_events(briefing, context, deps, keepalive_s=KEEPALIVE_S):
    """`briefing` (everything but the answer) first, then the answer as delta events and a final done or
    warning event. Failures after this point can't change the HTTP status, so they become a warning event."""
    yield sse("briefing", briefing)
    try:
        async for event in with_keepalive(stream_answer(context, deps), keepalive_s):
            if event is None:
                yield ": ping\n\n"
                continue
            name, value = event
            yield sse(name, {"text": value} if name == "delta" else {"answer": value} if name == "done" else value)
    except Exception:
        log.exception("streaming failed")
        yield sse("warning", UpstreamError("LLM", "unavailable").as_warning())
