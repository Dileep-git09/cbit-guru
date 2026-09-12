"""CBIT Guru — FastAPI application entry point.

Report §3.4.2 "Backend Framework": FastAPI acts as the orchestrator between
the React frontend, the RAG pipeline and (later) the automation engine.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.logging_config import configure_logging
from app.middleware import RequestContextMiddleware
from app.routers import admin, chat
from app.services import metrics, users, vectorstore

# Must run before anything else logs — sets up either plain human-readable
# lines or one-JSON-object-per-line (LOG_FORMAT in .env), and attaches the
# per-request request_id to every log record. See app/logging_config.py.
configure_logging()
log = logging.getLogger("cbit-guru")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Runs once at startup, before the app accepts any requests. Failing to
    # reach Qdrant here doesn't crash the app — it logs and continues, so
    # /api/health can still report "degraded" instead of the process refusing
    # to boot (useful if Qdrant Cloud has a brief blip on startup).
    try:
        await vectorstore.ensure_collection()
        log.info("Qdrant ready at %s", settings.qdrant_url)
    except Exception as exc:  # noqa: BLE001
        log.error("Qdrant unavailable at %s — %s", settings.qdrant_url, exc)

    # Creates the admin_users table (and seeds the first superadmin from
    # .env) on a fresh install — a no-op on every boot after that.
    await users.init()
    yield


app = FastAPI(
    title="CBIT Guru API",
    description="RAG-powered chat assistant for Chaitanya Bharathi Institute of Technology",
    version="1.0.0",
    lifespan=lifespan,
)

# Browsers block cross-origin requests by default; the React dev server runs
# on a different port (5173) than the API (8000), so CORS must explicitly
# allow it. settings.origins is read from ALLOWED_ORIGINS in .env, so this
# list changes per-environment without touching code.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added LAST so it's the OUTERMOST layer (Starlette wraps middleware in
# reverse registration order) — it needs to see and time the entire
# request/response cycle, CORS handling included, not just what's left
# after CORS has already run.
app.add_middleware(RequestContextMiddleware)

app.include_router(chat.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/metrics", tags=["observability"])
async def metrics_endpoint() -> Response:
    """Prometheus scrape target — request counts/latency, cache hit ratio,
    circuit breaker state. See app/services/metrics.py for what's tracked.

    Deliberately unauthenticated, matching standard Prometheus practice —
    a real deployment restricts this at the network layer (internal-only
    ingress, IP allowlist) rather than behind the same JWT auth as the
    admin API, since scrapers don't carry user credentials.
    """
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


@app.get("/api/health", tags=["health"])
async def health() -> dict:
    """Used by the frontend to show the green 'online' dot."""
    try:
        points = await vectorstore.count()
        return {"status": "ok", "qdrant": "connected", "points": points}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "qdrant": "unreachable", "detail": str(exc)}
