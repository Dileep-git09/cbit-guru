"""Prometheus-style metrics — the second pillar of observability alongside
structured logs (app/logging_config.py).

Logs answer "what happened on THIS one request." Metrics answer "what's the
trend" — error rate over the last hour, p95 latency, what fraction of chat
requests are cache hits — the kind of number a real dashboard or alert
watches continuously, without replaying every log line to compute it.

Exposed at GET /metrics in Prometheus's plain-text exposition format —
scrape it with Prometheus itself, or just `curl localhost:8000/metrics`
and read it directly; it's human-readable too.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

http_requests_total = Counter(
    "cbit_http_requests_total",
    "Total HTTP requests handled",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "cbit_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
)

# tier is one of: exact | semantic | miss — see services/cache.py. A rising
# "miss" ratio over time is the signal that would tell you the cache is no
# longer earning its keep (e.g. traffic patterns changed), long before
# anyone would notice by reading logs.
cache_hits_total = Counter(
    "cbit_cache_hits_total",
    "Response cache lookups by outcome",
    ["tier"],
)

# 1 = currently failing fast (open), 0 = normal (closed or half-open trial).
# One gauge per external dependency (gemini, cohere) — see
# services/circuitbreaker.py, which is what actually sets this.
circuit_breaker_open = Gauge(
    "cbit_circuit_breaker_open",
    "1 if the circuit breaker for this service is open, else 0",
    ["service"],
)


def record_http_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    http_requests_total.labels(method=method, path=path, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, path=path).observe(duration_seconds)


def record_cache_hit(tier: str) -> None:
    cache_hits_total.labels(tier=tier).inc()


def record_circuit_state(service: str, is_open: bool) -> None:
    circuit_breaker_open.labels(service=service).set(1 if is_open else 0)


def render() -> tuple[bytes, str]:
    """Returns (body, content_type) — exactly what a /metrics route needs
    to hand back to FastAPI's Response."""
    return generate_latest(), CONTENT_TYPE_LATEST
