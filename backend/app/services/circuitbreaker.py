"""A small circuit breaker for the Gemini and Cohere calls.

Without this, an actual outage plays out like this: every single request
(from every student) independently retries 5 times with exponential
backoff, discovers the dependency is still down, and only then fails — so
an outage doesn't just break the feature, it makes every request slow AND
keeps the concurrency semaphore (embeddings.py / llm.py) saturated with
calls that were never going to succeed, so nothing else can get through
either.

A circuit breaker remembers "this dependency has failed N times in a row"
and, once past that threshold, fails immediately for a cooldown window
instead of retrying — freeing the concurrency slots and giving every
caller a fast, honest "temporarily unavailable" instead of a slow,
eventual one. After the cooldown, exactly one call is let through as a
trial; if it succeeds the breaker closes again, if it fails the cooldown
restarts.

This is the classic three-state circuit breaker (closed / open / half-open)
distilled to what this project actually needs — no external library, no
extra dependency, just enough to change "outage = everyone waits ~10s to
be told no" into "outage = everyone is told no immediately."
"""
from __future__ import annotations

import time

from app.services import metrics


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int, cooldown_seconds: float):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        """True if calls should be rejected immediately without even trying."""
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self.cooldown_seconds:
            # Cooldown elapsed: half-open. Don't reset failures/opened_at
            # here — that only happens once a trial call actually succeeds
            # (record_success) or fails again (record_failure re-arms the
            # cooldown). This property is called once per attempt, so
            # returning False here is what lets exactly the next call
            # through as the trial.
            return False
        return True

    def record_success(self) -> None:
        was_open = self._opened_at is not None
        self._consecutive_failures = 0
        self._opened_at = None
        if was_open:
            metrics.record_circuit_state(self.name, is_open=False)

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold and self._opened_at is None:
            self._opened_at = time.monotonic()
            metrics.record_circuit_state(self.name, is_open=True)
