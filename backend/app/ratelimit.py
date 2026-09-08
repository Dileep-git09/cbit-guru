"""Per-IP rate limiter for the chat endpoints.

Why this exists: the free-tier Gemini/Cohere quotas are SHARED across every
visitor of this one deployment — they're not per-user allowances. Without a
limit, one script, browser bug, or a single student mashing "send" in a
loop can burn through the whole quota and lock out every other student at
once. This caps how much of that shared resource any single client (by IP)
can consume per minute, independent of the caching in services/cache.py —
even a cache-hitting flood of requests still counts against the limit here,
since the limiter runs before any cache lookup.

Redis-backed (fixed 60-second window, via INCR + EXPIRE) when REDIS_URL is
set, so the limit applies across every worker process/replica instead of
each one enforcing its own separate count — falls back to an in-memory
sliding window otherwise, which is correct for the single uvicorn worker
this project actually runs. See services/redisclient.py for the fallback
behaviour when Redis is configured but unreachable.

Fixed window vs. the in-memory path's true sliding window is a deliberate
trade-off: a client could in theory send `limit` requests right at the end
of one window and another `limit` right at the start of the next, briefly
exceeding the intended rate. That's an acceptable, well-known simplification
for what INCR+EXPIRE gives you almost for free — an exact sliding window in
Redis needs a sorted-set per key, which is more moving parts for a limit
that only exists to catch gross abuse, not to meter usage precisely.
"""
from __future__ import annotations

import time

from fastapi import HTTPException, Request, status

from app.config import settings
from app.services.redisclient import get_redis

# In-memory fallback: client IP -> timestamps of its requests within the
# current 60s window. Only used when Redis isn't configured/reachable.
_hits: dict[str, list[float]] = {}


async def rate_limit_chat(request: Request) -> None:
    limit = settings.chat_rate_limit_per_minute
    if limit <= 0:
        return  # 0 disables the limiter — e.g. for a controlled demo/viva

    ip = request.client.host if request.client else "unknown"
    redis = await get_redis()
    if redis is not None:
        await _check_redis(redis, ip, limit)
    else:
        _check_memory(ip, limit)


async def _check_redis(redis, ip: str, limit: int) -> None:
    bucket = int(time.time() // 60)  # a new counter key every calendar minute
    key = f"cbit:ratelimit:{ip}:{bucket}"
    count = await redis.incr(key)
    if count == 1:
        # Only the first request in this window needs to set the TTL —
        # 120s (not 60s) is deliberate slack so a slow INCR under load
        # can't race past its own EXPIRE and leave the key immortal.
        await redis.expire(key, 120)
    if count > limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests — please wait a moment (limit: {limit}/min).",
        )


def _check_memory(ip: str, limit: int) -> None:
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
