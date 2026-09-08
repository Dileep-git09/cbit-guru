"""Chat endpoint — the full RAG loop in one place.

query -> [cache check] -> embed -> Qdrant cosine search -> context fusion
-> Cohere -> answer (report §3.2.1 Figure 3.1 workflow, extended with a
response cache — see services/cache.py — for surviving many concurrent
students without exhausting the shared free-tier API quota). Two variants:
a plain request/response endpoint, and a Server-Sent Events (SSE) stream
for the UI's typing effect.
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, RelatedImage, SourceRef
from app.ratelimit import rate_limit_chat
from app.services import cache, embeddings, llm, retriever

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _sources(hits: list[dict]) -> list[SourceRef]:
    # Numbering here (i, start=1) matches the [1], [2]... labels retriever.py
    # put into the context block, so the LLM's inline citations line up with
    # the Sources list the frontend renders.
    return [
        SourceRef(
            n=i,
            label=h.get("file_name") or h.get("url") or h.get("type") or "source",
            type=h.get("type", "text"),
            url=h.get("url", "") or "",
            score=round(float(h.get("score", 0)), 3),
        )
        for i, h in enumerate(hits, start=1)
    ]


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _rl: None = Depends(rate_limit_chat)) -> ChatResponse:
    """Non-streaming variant — simpler to call from scripts/tests."""
    started = time.perf_counter()
    try:
        cached = cache.get_exact(req.message)
        if cached is None:
            qvec = await embeddings.embed_query(req.message)
            cached = cache.get_semantic(req.message, qvec)
        else:
            qvec = None  # exact hit — never needed the embedding at all

        if cached is not None:
            answer, sources, images, grounded = (
                cached.answer, cached.sources, cached.images, cached.grounded,
            )
        else:
            hits = await retriever.retrieve(req.message, user_id=req.user_id, qvec=qvec)
            context = retriever.build_context(hits["text"])
            answer = await llm.generate(
                req.message, context, [t.model_dump() for t in req.history]
            )
            sources = [s.model_dump() for s in _sources(hits["text"])]
            images = retriever.format_images(hits["images"])
            grounded = bool(context)
            cache.put(req.message, qvec, answer, sources, images, grounded)
    except RuntimeError as exc:
        # embeddings.py / llm.py raise RuntimeError for missing keys or
        # exhausted retries — surfaced as 503 (service unavailable), not 500,
        # since it's an upstream dependency issue, not a bug in our code.
        log.exception("chat failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(
        answer=answer,
        sources=[SourceRef(**s) for s in sources],
        images=[RelatedImage(**i) for i in images],
        grounded=grounded,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, _rl: None = Depends(rate_limit_chat)):
    """Server-Sent Events so the UI can type the answer out token by token.

    Retrieval happens up front (not streamed) because the frontend needs the
    full sources/images list before the first token arrives — that's the
    `meta` event. A cache hit skips straight to a single `token` event with
    the whole cached answer — instant, and a legitimate UX signal that a
    well-known fact was already on hand rather than freshly generated.
    """
    cached = cache.get_exact(req.message)
    qvec = None
    if cached is None:
        qvec = await embeddings.embed_query(req.message)
        cached = cache.get_semantic(req.message, qvec)

    if cached is not None:
        meta = {"sources": cached.sources, "images": cached.images, "grounded": cached.grounded}

        async def event_stream_cached():
            yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
            yield f"event: token\ndata: {json.dumps({'t': cached.answer})}\n\n"
            yield "event: done\ndata: {}\n\n"

        return StreamingResponse(
            event_stream_cached(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    hits = await retriever.retrieve(req.message, user_id=req.user_id, qvec=qvec)
    context = retriever.build_context(hits["text"])
    sources = [s.model_dump() for s in _sources(hits["text"])]
    images = retriever.format_images(hits["images"])
    grounded = bool(context)
    meta = {"sources": sources, "images": images, "grounded": grounded}

    async def event_stream():
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        accumulated = ""
        try:
            async for token in llm.generate_stream(
                req.message, context, [t.model_dump() for t in req.history]
            ):
                accumulated += token
                yield f"event: token\ndata: {json.dumps({'t': token})}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface any failure as an SSE event, not a crashed stream
            log.exception("stream failed")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
        else:
            # Only cache a complete, un-errored answer — a half-generated
            # response is exactly the sort of thing we don't want served
            # back out to the next ten students who ask the same question.
            cache.put(req.message, qvec, accumulated, sources, images, grounded)
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disables buffering in nginx-style proxies that would otherwise
            # hold the whole stream before forwarding it, defeating the
            # token-by-token effect entirely.
            "X-Accel-Buffering": "no",
        },
    )
