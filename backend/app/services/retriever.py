"""Retrieval layer — turns a question into grounded context + related images.

Report §3.3.2 "Information Retrieval Layer" and §3.5.3 "Multimodal Data
Handling": images live in the same vector space as text because each image is
represented by its filename plus the caption text surrounding it on the page
(see ingest.image_to_text). One query embedding therefore searches both.
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.services import embeddings, vectorstore


async def retrieve(
    question: str,
    user_id: str | None = None,
    top_k: int | None = None,
    qvec: list[float] | None = None,
) -> dict[str, Any]:
    """Embed the query once, then run two filtered searches: text and images.

    `qvec` lets a caller that already embedded the question — chat.py does,
    to check the semantic response cache before deciding whether retrieval
    is even needed — pass it straight through instead of paying for a
    second, identical Gemini call.
    """
    qvec = qvec if qvec is not None else await embeddings.embed_query(question)
    limit = top_k or settings.top_k

    text_hits = await vectorstore.search(
        qvec, limit=limit, user_id=user_id, score_threshold=settings.score_threshold
    )
    text_hits = [h for h in text_hits if h.get("type") != "image"]

    # If the threshold filtered everything out, retry unthresholded and let
    # the LLM decide — an "almost relevant" chunk beats answering with no
    # context at all, and the system prompt (llm.py) still forbids inventing
    # facts, so this loosening can't cause a confident wrong answer.
    if not text_hits:
        loose = await vectorstore.search(qvec, limit=limit, user_id=user_id)
        text_hits = [h for h in loose if h.get("type") != "image"]

    image_hits = await vectorstore.search(
        qvec,
        limit=settings.image_top_k,
        user_id=user_id,
        doc_type="image",
        score_threshold=settings.score_threshold,
    )

    return {"text": text_hits, "images": image_hits}


def build_context(hits: list[dict[str, Any]], max_chars: int = 12_000) -> str:
    """Concatenate retrieved chunks into a numbered, citable context block.

    The bracketed [n] labels here are exactly what the LLM is instructed to
    cite inline, and what the frontend's Sources dropdown displays — one
    numbering scheme threads through the whole answer.
    """
    parts: list[str] = []
    used = 0
    for i, hit in enumerate(hits, start=1):
        body = (hit.get("text") or "").strip()
        if not body:
            continue
        label = hit.get("file_name") or hit.get("url") or hit.get("type") or "source"
        block = f"[{i}] (source: {label})\n{body}"
        if used + len(block) > max_chars:
            # Stop rather than truncate mid-chunk — a half-cut chunk fed to
            # the LLM as "context" risks a half-cut, misleading fact.
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)


def format_images(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shape image hits for the frontend 'Related Images' strip."""
    out = []
    for h in hits:
        out.append(
            {
                "url": h.get("image_url") or h.get("url") or "",
                "caption": (h.get("caption") or h.get("file_name") or "").strip(),
                "tags": h.get("tags") or [],
                "score": round(float(h.get("score", 0)), 3),
            }
        )
    return [i for i in out if i["url"]]  # drop anything with no displayable image
