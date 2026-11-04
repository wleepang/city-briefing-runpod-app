"""Forward one Runpod job to the local llama-server. The job input is what Runpod's /openai/v1 route sends:
{"openai_route": "/v1/chat/completions", "openai_input": {...}}. A bare {"messages": [...]} or {"prompt": ...}
also works, for `runpodctl serverless run` and the native /run API."""
import os

import aiohttp

BASE = f"http://127.0.0.1:{os.getenv('LLAMA_PORT', '8081')}"
TIMEOUT = aiohttp.ClientTimeout(total=int(os.getenv("REQUEST_TIMEOUT", "3600")))


def normalize(job_input):
    """The (route, method, body) to call on llama-server."""
    if "openai_input" in job_input:
        return job_input.get("openai_route") or "/v1/chat/completions", "POST", job_input["openai_input"]
    if "openai_route" in job_input:  # read-only routes such as /v1/models
        return job_input["openai_route"], "GET", None
    if "messages" in job_input or "prompt" in job_input:
        route = "/v1/chat/completions" if "messages" in job_input else "/v1/completions"
        return route, "POST", {k: v for k, v in job_input.items() if k != "sampling_params"} | (job_input.get("sampling_params") or {})
    raise ValueError("input needs openai_input, openai_route, messages or prompt")


async def handler(job):
    try:
        route, method, body = normalize(job.get("input") or {})
    except ValueError as err:
        yield {"error": str(err)}
        return
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.request(method, BASE + route, json=body) as res:
                if res.status >= 400:
                    yield {"error": {"status": res.status, "message": (await res.text())[:500]}}
                elif body and body.get("stream") is True:
                    async for chunk in res.content.iter_any():  # raw server-sent-event text, passed through
                        yield chunk.decode("utf-8", errors="replace")
                else:
                    yield await res.json()
    except aiohttp.ClientError as err:
        yield {"error": f"Request to llama-server failed: {err}"}
