"""Structured logging setup — the difference between "read the log file
top to bottom hoping to spot the right line" and "grep every log line for
this one request_id and see its whole story."

Two things this gives you that plain `logging.basicConfig` doesn't:

1. A request_id attached to EVERY log line emitted while handling one HTTP
   request — including lines logged deep inside services/embeddings.py or
   services/llm.py that have no idea an HTTP request even exists. This
   works via a contextvar (request_id_ctx) set once per request by
   app/middleware.py's RequestIDMiddleware, plus a logging Filter that
   reads it back out for every record. No log call anywhere in the
   codebase needs to know about request IDs at all.

2. Optional JSON output (LOG_FORMAT=json in .env): one JSON object per log
   line instead of a formatted string. A real deployment ships this to a
   log aggregator that indexes fields (level, request_id, latency_ms, ...)
   for querying — "show me every 5xx in the last hour" is a query, not a
   grep, once logs are structured. LOG_FORMAT=plain (the default) keeps
   the human-readable one-liner that's nicer to stare at in a terminal
   while developing.
"""
from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime, timezone

from app.config import settings

# Set once per request by app/middleware.py; read by _RequestIdFilter below
# for every log record emitted anywhere while that request is in flight.
# Defaults to "-" outside of any request (startup logs, background scripts).
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# Attributes every stdlib LogRecord already has — anything else on a record
# (latency_ms=, cache_tier=, etc., passed via `extra=`) is a field WE added
# on purpose, so the JSON formatter includes it automatically instead of
# every call site needing to remember to serialise it manually.
_STANDARD_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        # Anything passed via logging's `extra={...}` shows up as ordinary
        # attributes on the record — pull those through too, so a call like
        # log.info("chat done", extra={"latency_ms": 812, "cache_tier": "exact"})
        # produces a JSON line with latency_ms and cache_tier as real,
        # queryable fields, not text buried in the message string.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_KEYS and key != "request_id":
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Call once, at process startup, before anything else logs."""
    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())

    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s | %(message)s"
            )
        )

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()  # avoid duplicate lines if this ever runs twice (e.g. --reload)
    root.addHandler(handler)
