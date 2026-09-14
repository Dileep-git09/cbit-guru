"""Qdrant vector store — where embedded chunks live and get searched.

Report §3.4.2 "Vector Database": collection `cbit_guru_rag`, 3072-dim vectors,
cosine distance, HNSW index, metadata payload carrying user_id / doc_id /
chunk_index / type so results can be filtered (e.g. "images only") and scoped
per-user without needing a second database.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import settings

log = logging.getLogger(__name__)

_client: AsyncQdrantClient | None = None


def get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        if settings.qdrant_url in (":memory:", "memory"):
            # In-process store, no server needed — used by smoke_test.py so
            # the whole pipeline can be verified offline, at zero API cost.
            _client = AsyncQdrantClient(":memory:")
        else:
            _client = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=60,
            )
    return _client


async def ping() -> bool:
    """Cheap reachability check for the readiness probe (main.py's
    /api/health/ready) — lists collections rather than counting points, so
    it stays fast regardless of how large the knowledge base grows. Never
    raises: a connectivity problem is exactly the "not ready" signal the
    caller wants, not an exception to handle.
    """
    try:
        await get_client().get_collections()
        return True
    except Exception:  # noqa: BLE001
        return False


async def ensure_collection() -> None:
    """Create the collection on first boot if it does not already exist.

    Called once from main.py's startup lifespan — this is why the app can be
    pointed at a brand-new empty Qdrant Cloud cluster and just work.
    """
    client = get_client()
    existing = {c.name for c in (await client.get_collections()).collections}
    if settings.qdrant_collection in existing:
        return

    await client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=models.VectorParams(
            size=settings.embedding_dim,           # must match the embedder's output
            distance=models.Distance.COSINE,       # angle, not magnitude — see report §3.4.4
        ),
        # HNSW (Hierarchical Navigable Small World) is the approximate-nearest-
        # neighbour graph index Qdrant builds over the vectors. m=16 controls
        # how many graph edges each node keeps; ef_construct=128 controls how
        # thoroughly the graph is built. Both are Qdrant's own sane defaults
        # for a knowledge base this size — no need to tune further.
        hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
    )
    # Payload indexes turn "give me only points where type=image" from a full
    # scan into an indexed lookup — this is what keeps admin `browse()` and
    # the image/text split in retriever.py fast as the KB grows.
    for field in ("user_id", "doc_id", "type", "source"):
        try:
            await client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("payload index %s: %s", field, exc)

    log.info("Created Qdrant collection %s", settings.qdrant_collection)


async def upsert_chunks(
    vectors: list[list[float]],
    payloads: list[dict[str, Any]],
) -> int:
    """Store vectors + metadata. Returns the number of points written."""
    if not vectors:
        return 0
    client = get_client()
    points = [
        # A random UUID per point means re-ingesting the same source twice
        # creates duplicates rather than silently overwriting — dedup is a
        # known limitation, noted so it doesn't look accidental in the viva.
        models.PointStruct(id=str(uuid.uuid4()), vector=vec, payload=payload)
        for vec, payload in zip(vectors, payloads, strict=True)
    ]
    await client.upsert(
        collection_name=settings.qdrant_collection, points=points, wait=True
    )
    return len(points)


def _build_filter(
    user_id: str | None = None,
    doc_type: str | None = None,
) -> models.Filter | None:
    """Build the Qdrant metadata filter for a search.

    A chunk is visible to a search scoped to `user_id` if it either belongs
    to that user OR was ingested globally (user_id="system") — that's the
    `should` (OR) clause. `doc_type` narrows further with a `must` (AND),
    e.g. restricting a search to only image-type points.
    """
    must: list[models.Condition] = []
    if user_id:
        return models.Filter(
            should=[
                models.FieldCondition(
                    key="user_id", match=models.MatchValue(value=user_id)
                ),
                models.FieldCondition(
                    key="user_id", match=models.MatchValue(value="system")
                ),
            ],
            must=(
                [
                    models.FieldCondition(
                        key="type", match=models.MatchValue(value=doc_type)
                    )
                ]
                if doc_type
                else None
            ),
        )
    if doc_type:
        must.append(
            models.FieldCondition(key="type", match=models.MatchValue(value=doc_type))
        )
    return models.Filter(must=must) if must else None


async def search(
    query_vector: list[float],
    limit: int | None = None,
    user_id: str | None = None,
    doc_type: str | None = None,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Top-k cosine similarity search — report §3.4.2 `query_points()`."""
    client = get_client()
    result = await client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        limit=limit or settings.top_k,
        query_filter=_build_filter(user_id, doc_type),
        score_threshold=score_threshold,   # None = no filtering, return everything
        with_payload=True,
    )
    return [
        {"score": p.score, "id": str(p.id), **(p.payload or {})} for p in result.points
    ]


async def count() -> int:
    """Total points in the collection — powers the /api/health point count
    and the admin stats card (report Figure 3.8)."""
    client = get_client()
    return (
        await client.count(collection_name=settings.qdrant_collection, exact=True)
    ).count


async def browse(limit: int = 250, offset: str | None = None) -> dict[str, Any]:
    """Page through stored chunks for the admin 'Browse Data' tab.

    Uses Qdrant's scroll API (cursor-based pagination) rather than search —
    there's no query vector here, we just want to see what's stored.
    """
    client = get_client()
    points, next_offset = await client.scroll(
        collection_name=settings.qdrant_collection,
        limit=limit,
        offset=offset,
        with_payload=True,
        with_vectors=False,   # vectors are 3072 floats each — pointless to ship to the UI
    )
    return {
        "items": [
            {
                "id": str(p.id),
                "doc_id": (p.payload or {}).get("doc_id", ""),
                "source": (p.payload or {}).get("type", "unknown"),
                "file_name": (p.payload or {}).get("file_name", ""),
                "url": (p.payload or {}).get("url", ""),
                "preview": ((p.payload or {}).get("text", ""))[:180],
            }
            for p in points
        ],
        "next_offset": str(next_offset) if next_offset else None,
    }


async def delete_by_doc(doc_id: str) -> None:
    """Delete every chunk belonging to one ingested document (admin panel)."""
    client = get_client()
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="doc_id", match=models.MatchValue(value=doc_id)
                    )
                ]
            )
        ),
    )
