"""Response cache — cuts real API calls for repeat/near-repeat questions,
which is the single biggest lever for surviving free-tier quota with many
concurrent students asking the same handful of popular questions ("where
is CBIT located", "what are the placement stats", the suggested questions
the UI itself puts in front of every visitor).

Two tiers, checked in order:

  * exact    — the normalised question string maps straight to a cached
               response. Zero-cost lookup, catches literal repeats.
               Redis-backed when REDIS_URL is set (see redisclient.py),
               so this tier is shared across multiple worker processes;
               falls back to an in-memory dict otherwise.
  * semantic — compares the NEW query's embedding (already computed for
               retrieval anyway) against a capped list of past
               (embedding, response) pairs via cosine similarity. Catches
               paraphrases the exact cache misses ("Where's CBIT?" vs
               "Where is CBIT located?"). Always in-memory: a real
               vector-similarity index belongs in a proper vector store
               (this project already has one — Qdrant — for the actual
               knowledge base), not reimplemented on top of Redis's basic
               data structures for what's at most a few hundred entries.
               Per-process, single-worker only — a documented limitation,
               not a silent one.

The semantic tier is bucketed by SCRIPT (Latin / Devanagari / Telugu), not
just meaning. This was found the hard way during multilingual testing:
Gemini's embedding model places "Where is CBIT located?" and its Hindi and
Telugu translations very close together in vector space (same meaning,
different script) — well above the similarity threshold — so a Telugu
question was getting served a cached HINDI answer purely because the
underlying question was semantically the same one. Bucketing by script
means the cosine comparison only ever runs against entries whose ORIGINAL
question was written in the same script, so a same-meaning-different-
script query can no longer return an answer in the wrong language. It does
NOT catch every case (Hinglish and Tinglish are both Latin script but
different languages) — a known, documented limitation, not a silent gap.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

from app.config import settings
from app.services.redisclient import get_redis


@dataclass
class CachedResponse:
    answer: str
    sources: list[dict]
    images: list[dict]
    grounded: bool
    ts: float = field(default_factory=time.monotonic)


# In-memory fallback for the exact tier (used whenever Redis isn't
# configured/reachable), and the ALWAYS-in-memory semantic tier.
_exact: dict[str, CachedResponse] = {}
_semantic: dict[str, list[tuple[list[float], CachedResponse]]] = {}


def _normalize(question: str) -> str:
    return " ".join(question.strip().lower().split())


def _redis_key(question: str) -> str:
    # Hashed rather than used raw: keeps key length bounded regardless of
    # how long a question is, and sidesteps ever worrying about characters
    # Redis keys can't contain (there aren't really any, but why think about it).
    return f"cbit:cache:exact:{hashlib.sha256(_normalize(question).encode()).hexdigest()}"


def _script_bucket(question: str) -> str:
    """Classify the dominant Unicode script of a question. Mirrors the
    frontend's detectLang() in useVoice.js, which does the same thing for
    the model's REPLY when picking a text-to-speech voice — this is the
    same idea applied to the incoming QUESTION, to keep the cache from
    matching across languages.
    """
    if any("ఀ" <= ch <= "౿" for ch in question):
        return "telugu"
    if any("ऀ" <= ch <= "ॿ" for ch in question):
        return "devanagari"
    return "latin"  # English, Hinglish, Tinglish — see the limitation noted above


def _fresh(entry: CachedResponse) -> bool:
    return (time.monotonic() - entry.ts) < settings.cache_ttl_seconds


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def get_exact(question: str) -> CachedResponse | None:
    if not settings.cache_enabled:
        return None

    redis = await get_redis()
    if redis is not None:
        raw = await redis.get(_redis_key(question))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        # Redis's own TTL (set via SETEX in put()) already handles
        # expiry — there's no separate freshness check needed here, unlike
        # the in-memory path below.
        return CachedResponse(**data)

    key = _normalize(question)
    entry = _exact.get(key)
    if entry is None:
        return None
    if not _fresh(entry):
        _exact.pop(key, None)  # expired — evict lazily on next lookup
        return None
    return entry


def get_semantic(question: str, qvec: list[float]) -> CachedResponse | None:
    """Linear scan is fine here: each script bucket is capped at a few
    hundred entries (cache_max_semantic_entries), so this is at most a few
    hundred dot products per cache-miss lookup — trivial next to a network
    round trip to Gemini/Cohere, and avoids pulling in a vector-index
    library for something this small. Always in-memory — see module
    docstring for why this tier doesn't move to Redis.
    """
    if not settings.cache_enabled:
        return None
    bucket = _semantic.get(_script_bucket(question), [])
    best: tuple[float, CachedResponse] | None = None
    for vec, entry in bucket:
        if not _fresh(entry):
            continue
        score = _cosine(qvec, vec)
        if score >= settings.cache_semantic_threshold and (best is None or score > best[0]):
            best = (score, entry)
    return best[1] if best else None


async def put(
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

    redis = await get_redis()
    if redis is not None:
        payload = json.dumps(
            {"answer": answer, "sources": sources, "images": images, "grounded": grounded}
        )
        await redis.setex(_redis_key(question), settings.cache_ttl_seconds, payload)
    else:
        _exact[_normalize(question)] = entry

    # Semantic tier is always in-memory regardless of the exact tier's
    # backend — see module docstring.
    bucket = _semantic.setdefault(_script_bucket(question), [])
    bucket.append((qvec, entry))
    if len(bucket) > settings.cache_max_semantic_entries:
        bucket.pop(0)  # oldest-first eviction once over the cap


async def stats() -> dict:
    redis = await get_redis()
    if redis is not None:
        exact_entries = len(await redis.keys("cbit:cache:exact:*"))
        backend = "redis"
    else:
        exact_entries = len(_exact)
        backend = "memory"
    return {
        "exact_entries": exact_entries,
        "semantic_entries": sum(len(b) for b in _semantic.values()),
        "exact_backend": backend,
    }


async def clear() -> None:
    """Used by tests to reset state between runs."""
    _exact.clear()
    _semantic.clear()
    redis = await get_redis()
    if redis is not None:
        keys = await redis.keys("cbit:cache:exact:*")
        if keys:
            await redis.delete(*keys)
