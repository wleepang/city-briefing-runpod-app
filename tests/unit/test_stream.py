"""The server-sent-event layer: event order, keepalives while the model is slow, and failures after the first event."""
import asyncio
import json

import pytest

from app.stream import briefing_events, sse, with_keepalive

from ..conftest import json_response, make_test_deps, run, sse_response

BRIEFING = {"city": {"name": "Lisbon"}, "answer": None, "warnings": []}
CONTEXT = {"Open-Meteo": {}, "Wikipedia": None}


def parse(chunks):
    """[(event, data)] from the text of an SSE stream; comment lines come back as ("ping", None)."""
    out = []
    for block in "".join(chunks).strip().split("\n\n"):
        if block.startswith(":"):
            out.append(("ping", None))
        else:
            event, data = block.split("\n", 1)
            out.append((event.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def events(deps, **kw):
    async def collect():
        return [c async for c in briefing_events(BRIEFING, CONTEXT, deps, **kw)]
    return parse(run(collect()))


def test_sse_formats_one_event_and_keeps_newlines_and_non_ascii_inside_the_json():
    assert sse("delta", {"text": "a\nb é"}) == 'event: delta\ndata: {"text": "a\\nb é"}\n\n'


def test_the_data_comes_first_then_the_answer_in_pieces_then_the_whole_answer():
    deps, _ = make_test_deps({"llm.test": lambda r: sse_response("Nice ", "day.")})
    assert events(deps) == [
        ("briefing", BRIEFING), ("delta", {"text": "Nice "}), ("delta", {"text": "day."}), ("done", {"answer": "Nice day."}),
    ]


def test_a_model_failure_ends_the_stream_with_a_warning_and_the_data_is_already_sent():
    deps, _ = make_test_deps({"llm.test": lambda r: json_response({}, 503)})
    assert events(deps) == [
        ("briefing", BRIEFING), ("warning", {"service": "LLM", "kind": "unavailable", "message": "The AI writer is currently unavailable."}),
    ]


def test_over_budget_sends_the_budget_warning_and_never_calls_the_model():
    deps, calls = make_test_deps(llm_daily_cap=1)
    deps.budget.try_consume()  # the one allowed call is spent
    kinds = [e for e, _ in events(deps)]
    assert kinds == ["briefing", "warning"]
    assert not [c for c in calls if c.url.host == "llm.test"]


def test_an_unexpected_error_mid_stream_becomes_a_warning_instead_of_a_broken_connection():
    deps, _ = make_test_deps({"llm.test": lambda r: sse_response("hi")})

    def boom(*a, **k):
        raise RuntimeError("bug")

    deps.budget.try_consume = boom
    assert [e for e, _ in events(deps)] == ["briefing", "warning"]


def test_keepalive_pings_while_waiting_and_passes_items_through_in_order():
    async def slow():
        yield 1
        await asyncio.sleep(0.12)
        yield 2

    async def collect():
        return [x async for x in with_keepalive(slow(), 0.03)]

    got = run(collect())
    assert [x for x in got if x is not None] == [1, 2] and got.count(None) >= 2


def test_the_keepalive_stops_waiting_on_the_model_when_the_client_leaves():
    stopped = []

    async def never():
        try:
            await asyncio.sleep(60)
            yield 1
        finally:
            stopped.append(True)

    async def leave_after_first_ping():
        gen = with_keepalive(never(), 0.01)
        assert await gen.__anext__() is None
        await gen.aclose()

    run(leave_after_first_ping())
    assert stopped == [True]
