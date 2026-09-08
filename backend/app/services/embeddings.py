"""Gemini embedding service — turns text into vectors.

Report §3.4.2 "Embedding Model": vectors are produced with
`genai.embed_content()` using `gemini-embedding-001` at 3072 dimensions.

Report §3.4.1 (fault tolerance): up to five attempts, exponential backoff
starting at 0.3s whenever the API answers 429 (rate limit) — this is what
lets a 150-page ingest survive free-tier quota bursts unattended.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Literal

from google import genai
from google.genai import types

from app.config import settings
from app.services.circuitbreaker import CircuitBreaker

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_DELAY = 0.3  # seconds — doubles each retry: 0.3, 0.6, 1.2, 2.4s

# Gemini embeds queries and documents slightly differently for better recall —
# a question and the passage that answers it aren't phrased the same way, so
# the model gets told which side of that pair this text is.
TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

_client: genai.Client | None = None
_global_sem: asyncio.Semaphore | None = None
_breaker = CircuitBreaker(
    "gemini",
    failure_threshold=settings.circuit_breaker_threshold,
    cooldown_seconds=settings.circuit_breaker_cooldown_seconds,
)


def _get_client() -> genai.Client:
    # Lazy singleton: only construct the client (and validate the key exists)
    # the first time it's actually needed, not at import time.
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set — check backend/.env")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _get_global_sem() -> asyncio.Semaphore:
    """Process-wide gate on *actually in-flight* Gemini calls, regardless of
    which code path they came from (a single chat question, or one file's
    worth of ingest chunks). This is what stands between "many students ask
    a question at the same instant" and "everyone gets a 429" — the extra
    requests wait their turn here instead of all firing at once.
    """
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(settings.gemini_max_concurrency)
    return _global_sem


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate limit" in text


async def embed_one(text: str, task_type: TaskType = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed a single string into a 3072-dim vector, with backoff on 429."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Cannot embed empty text")

    if _breaker.is_open:
        # Fail immediately instead of spending 5 retries (several seconds)
        # discovering what we already know: Gemini has been failing
        # repeatedly. Every caller gets this fast, honest answer during an
        # outage instead of a slow one, and the concurrency semaphore stays
        # free for the trial request that eventually reopens the breaker.
        raise RuntimeError(
            "Gemini is temporarily unavailable (circuit breaker open) — try again shortly"
        )

    client = _get_client()
    last_exc: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            # The global semaphore caps how many of these are truly in flight
            # at once across the whole process; everyone else queues here
            # rather than firing at Gemini simultaneously.
            async with _get_global_sem():
                # The google-genai SDK is sync-only, so we push the network call
                # onto a worker thread with asyncio.to_thread — this keeps FastAPI's
                # event loop free to serve other requests while we wait on Gemini.
                # wait_for bounds the whole thing: to_thread's underlying OS
                # thread can't be cancelled mid-call, but wait_for still stops
                # US waiting on it and frees the semaphore slot, so one truly
                # hung request can't permanently starve the other 4.
                resp = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.embed_content,
                        model=settings.embedding_model,
                        contents=text,
                        config=types.EmbedContentConfig(
                            task_type=task_type,
                            output_dimensionality=settings.embedding_dim,
                        ),
                    ),
                    timeout=settings.api_call_timeout_seconds,
                )
            _breaker.record_success()
            return list(resp.embeddings[0].values)
        except Exception as exc:  # noqa: BLE001 — we retry on anything transient
            last_exc = exc
            if attempt == MAX_ATTEMPTS - 1 or not _is_rate_limit(exc):
                break
            # Exponential backoff + small random jitter, so many concurrent
            # requests hitting a 429 at once don't all retry at the same instant.
            delay = BASE_DELAY * (2**attempt) + random.uniform(0, 0.1)
            log.warning(
                "Gemini embed rate-limited (attempt %d/%d); sleeping %.2fs",
                attempt + 1, MAX_ATTEMPTS, delay,
            )
            await asyncio.sleep(delay)

    _breaker.record_failure()
    raise RuntimeError(f"Embedding failed after {MAX_ATTEMPTS} attempts: {last_exc}")


async def embed_many(
    texts: list[str],
    task_type: TaskType = "RETRIEVAL_DOCUMENT",
    concurrency: int = 4,
) -> list[list[float]]:
    """Embed a list of strings with bounded concurrency (keeps us under quota).

    Without the semaphore, ingesting a 150-page site would fire hundreds of
    requests at once and get rate-limited immediately. Capping at 4 in flight
    is the balance between "fast" and "free tier doesn't reject us".
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> list[float]:
        async with sem:
            return await embed_one(t, task_type)

    return await asyncio.gather(*(_one(t) for t in texts))


async def embed_query(text: str) -> list[float]:
    """Embed a user question (uses RETRIEVAL_QUERY task type for better recall)."""
    return await embed_one(text, task_type="RETRIEVAL_QUERY")
