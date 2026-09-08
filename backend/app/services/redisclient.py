"""Lazy, optional Redis connection shared by cache.py and ratelimit.py.

Both modules work correctly with zero configuration — REDIS_URL empty
means "in-memory only," which is what this project actually runs day to
day. Redis is opt-in: set REDIS_URL to share the response cache and rate
limits across multiple worker processes or replicas, something the plain
in-memory dicts in each module can't do (each process would otherwise keep
its own separate, disconnected state).

If Redis is configured but unreachable when first needed — wrong URL, the
server isn't running, whatever — we log a warning once and fall back to
in-memory instead of failing the whole app over what is, after all, just a
performance optimisation. A cache/rate-limiter that's briefly per-process
during a Redis blip is a much smaller problem than a chatbot that won't
boot.
"""
from __future__ import annotations

import logging

from app.config import settings

log = logging.getLogger(__name__)

_client = None
_unavailable = False


async def get_redis():
    """Returns a connected async Redis client, or None if Redis isn't
    configured / isn't installed / couldn't be reached — callers must
    treat None as "use the in-memory fallback," not as an error."""
    global _client, _unavailable
    if _unavailable or not settings.redis_url:
        return None
    if _client is None:
        try:
            import redis.asyncio as aioredis
        except ImportError:
            log.warning(
                "REDIS_URL is set but the 'redis' package isn't installed "
                "(pip install redis) — falling back to in-memory"
            )
            _unavailable = True
            return None
        try:
            client = aioredis.from_url(
                settings.redis_url, decode_responses=True, socket_connect_timeout=2
            )
            await client.ping()
            _client = client
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Redis at %s unreachable (%s) — falling back to in-memory",
                settings.redis_url, exc,
            )
            _unavailable = True
            return None
    return _client


def reset() -> None:
    """Used by tests to clear cached connection state between runs."""
    global _client, _unavailable
    _client = None
    _unavailable = False
