import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .briefing import build_briefing, gather_briefing, make_deps
from .config import Config, load_config
from .errors import HttpError, UpstreamError, error_response
from .stream import briefing_events
from .validate import parse_briefing_query

log = logging.getLogger("city-briefing")


class NoCacheStatic(StaticFiles):
    """Browsers cache files that carry no cache rules for a guessed time, so a returning visitor can run the new
    index.html against an old render.js. `no-cache` means "check with the server first"; unchanged files still
    answer 304 from their ETag."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"


def create_app(config: Config | None = None, client: httpx.AsyncClient | None = None) -> FastAPI:
    """Wire HTTP to the workflow. Only this module knows about FastAPI. `client` is injectable so tests can
    mock every upstream call (httpx.MockTransport)."""
    config = config or load_config()
    owns_client = client is None
    deps = make_deps(config, client or httpx.AsyncClient())

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if owns_client:
            await deps.client.aclose()

    app = FastAPI(title="City Briefing", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps

    @app.get("/health")
    async def health():
        return {"ok": True, "model": config.llm.model}

    @app.get("/api/briefing")
    async def briefing(request: Request):
        q = parse_briefing_query(request.query_params)
        return await build_briefing(q["city"], q["id"], deps)

    @app.get("/api/briefing/stream")
    async def briefing_stream(request: Request):
        """Same briefing as server-sent events: the data first, then the answer as it is written. Errors found
        before the first event (bad input, unknown city, Open-Meteo down) are ordinary JSON error responses."""
        q = parse_briefing_query(request.query_params)
        briefing, context = await gather_briefing(q["city"], q["id"], deps)
        return StreamingResponse(
            briefing_events(briefing, context, deps),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # One mapping for every failure (see errors.error_response); only logging differs.
    def respond(err):
        status, body, headers = error_response(err)
        return JSONResponse(body, status_code=status, headers=headers)

    @app.exception_handler(HttpError)
    async def on_http_error(_req, err):
        return respond(err)

    @app.exception_handler(UpstreamError)
    async def on_upstream_error(_req, err):
        log.error("[upstream] %s", err)
        return respond(err)

    @app.exception_handler(Exception)
    async def on_unexpected(_req, err):
        log.exception("unhandled error", exc_info=err)
        return respond(err)

    # Mounted last so the API routes above take priority.
    app.mount("/", NoCacheStatic(directory=PUBLIC_DIR, html=True), name="static")
    return app
