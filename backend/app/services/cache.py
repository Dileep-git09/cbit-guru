"""In-process response cache — cuts real API calls for repeat/near-repeat
questions, which is the single biggest lever for surviving free-tier quota
with many concurrent students asking the same handful of popular questions
("where is CBIT located", "what are the placement stats", the suggested
questions the UI itself puts in front of every visitor).

Two tiers, checked in order:

  * exact    — the normalised question string maps straight to a cached
               response. Zero-cost lookup, catches literal repeats.
  * semantic — compares the NEW query's embedding (already computed for
               retrieval anyway) against a capped list of past
               (embedding, response) pairs via cosine similarity. Catches
               paraphrases the exact cache misses ("Where's CBIT?" vs
               "Where is CBIT located?").

Both are per-process, in-memory, and TTL-bound — correct for a single
uvicorn worker, which is what this project actually runs. Running multiple
worker processes or replicas would need a shared store (Redis) instead,
since each process would otherwise keep its own separate cache — that's the
scaling path documented in docs/BUILD_GUIDE.md rather than implemented
here, since a single process is the real deployment target.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import settings


@dataclass
class CachedResponse:
    answer: str
    sources: list[dict]
    images: list[dict]
    grounded: bool
    ts: float = field(default_factory=time.monotonic)


_exact: dict[str, CachedResponse] = {}
_semantic: list[tuple[list[float], CachedResponse]] = []


def _normalize(question: str) -> str:
    return " ".join(question.strip().lower().split())


def _fresh(entry: CachedResponse) -> bool:
    return (time.monotonic() - entry.ts) < settings.cache_ttl_seconds


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def get_exact(question: str) -> CachedResponse | None:
    if not settings.cache_enabled:
        return None
    key = _normalize(question)
    entry = _exact.get(key)
    if entry is None:
        return None
    if not _fresh(entry):
        _exact.pop(key, None)  # expired — evict lazily on next lookup
        return None
    return entry


def get_semantic(qvec: list[float]) -> CachedResponse | None:
    """Linear scan is fine here: the cache is capped at a few hundred
    entries (cache_max_semantic_entries), so this is a few hundred dot
    products per cache-miss lookup — trivial next to a network round trip
    to Gemini/Cohere, and avoids pulling in a vector-index library for
    something this small."""
    if not settings.cache_enabled:
        return None
    best: tuple[float, CachedResponse] | None = None
    for vec, entry in _semantic:
        if not _fresh(entry):
            continue
        score = _cosine(qvec, vec)
        if score >= settings.cache_semantic_threshold and (best is None or score > best[0]):
            best = (score, entry)
    return best[1] if best else None


def put(
    question: str,
    qvec: list[float],
    answer: str,
    sources: list[dict],
    images: list[dict],
    grounded: bool,
) -> None:
    if not settings.cache_enabled:
        return
    entry = CachedResponse(answer=answer, sources=sources, images=images, grounded=grounded)
    _exact[_normalize(question)] = entry

    _semantic.append((qvec, entry))
    if len(_semantic) > settings.cache_max_semantic_entries:
        _semantic.pop(0)  # oldest-first eviction once over the cap


def stats() -> dict:
    return {"exact_entries": len(_exact), "semantic_entries": len(_semantic)}


def clear() -> None:
    """Used by tests to reset state between runs."""
    _exact.clear()
    _semantic.clear()
