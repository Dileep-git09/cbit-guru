"""Request-ID tagging + structured access logging, as one pure-ASGI
middleware.

Deliberately NOT Starlette's `BaseHTTPMiddleware` — that wrapper buffers the
entire response before forwarding it, which would silently break the SSE
streaming `/api/chat/stream` uses for the UI's token-by-token typing effect
(the whole answer would arrive in one burst instead of streaming). A raw
ASGI middleware that wraps `send` sees each chunk as it goes out and never
buffers, so streaming keeps working exactly as before.

What it does, per request:
  1. Reads an incoming `X-Request-ID` header if the caller sent one
     (useful if this API sits behind a gateway/load balancer that already
     assigns one), otherwise generates a short one.
  2. Sets that ID on a contextvar (app/logging_config.request_id_ctx) —
     every log line emitted anywhere while handling this request picks it
     up automatically, with zero changes needed at each log call site.
  3. Echoes it back as a response header, so a caller (or the browser
     devtools network tab) can correlate "this specific request" with
     "these specific server log lines."
  4. Logs one structured line and records Prometheus metrics once the
     response finishes — method, normalised path, status, duration.
"""
from __future__ import annotations

import logging
import re
import time
import uuid

from app.logging_config import request_id_ctx
from app.services import metrics

log = logging.getLogger("cbit-guru.access")

# Collapses path segments that look like an id/hash into a fixed
# placeholder before they become a metrics label — e.g.
# /api/admin/users/5b4cc63b.../password -> /api/admin/users/{id}/password.
# Without this, one label per distinct user/doc id would make the
# cardinality of http_requests_total grow forever instead of staying
# bounded to "one series per route."
_ID_SEGMENT = re.compile(r"^[0-9a-fA-F]{6,}$")


def _normalize_path(path: str) -> str:
    return "/".join("{id}" if _ID_SEGMENT.match(seg) else seg for seg in path.split("/"))


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming_id = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-request-id"),
            None,
        )
        request_id = incoming_id or uuid.uuid4().hex[:12]
        token = request_id_ctx.set(request_id)

        method = scope.get("method", "")
        path = _normalize_path(scope.get("path", ""))
        started = time.perf_counter()
        status_holder = {"code": 500}  # overwritten below; 500 only survives if send() never ran

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                message["headers"].append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_seconds = time.perf_counter() - started
            status = status_holder["code"]
            log.info(
                "request completed",
                extra={
                    "http_method": method,
                    "http_path": path,
                    "http_status": status,
                    "duration_ms": round(duration_seconds * 1000, 1),
                },
            )
            metrics.record_http_request(method, path, status, duration_seconds)
            request_id_ctx.reset(token)
