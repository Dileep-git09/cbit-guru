"""Per-IP sliding-window rate limiter for the chat endpoints.

Why this exists: the free-tier Gemini/Cohere quotas are SHARED across every
visitor of this one deployment — they're not per-user allowances. Without a
limit, one script, browser bug, or a single student mashing "send" in a
loop can burn through the whole quota and lock out every other student at
once. This caps how much of that shared resource any single client (by IP)
can consume per minute, independent of the caching in services/cache.py —
even a cache-hitting flood of requests still counts against the limit here,
since the limiter runs before any cache lookup.

In-memory and per-process, matching services/cache.py's approach: correct
for a single uvicorn worker, which is what this project actually runs. A
multi-instance deployment would need a shared store (Redis) so the limit
applies across processes/replicas too — see docs/BUILD_GUIDE.md.
"""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, status

from app.config import settings

# client IP -> timestamps of its requests within the current 60s window
_hits: dict[str, list[float]] = {}


async def rate_limit_chat(request: Request) -> None:
    limit = settings.chat_rate_limit_per_minute
    if limit <= 0:
        return  # 0 disables the limiter — e.g. for a controlled demo/viva

    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window_start = now - 60.0

    timestamps = [t for t in _hits.get(ip, []) if t > window_start]
    if len(timestamps) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests — please wait a moment (limit: {limit}/min).",
        )
    timestamps.append(now)
    _hits[ip] = timestamps


def reset() -> None:
    """Used by tests to clear state between runs."""
    _hits.clear()
