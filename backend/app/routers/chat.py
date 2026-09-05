"""Chat endpoint — the full RAG loop in one place.

query -> embed -> Qdrant cosine search -> context fusion -> Cohere -> answer
(report §3.2.1 Figure 3.1 workflow). Two variants: a plain request/response
endpoint, and a Server-Sent Events (SSE) stream for the UI's typing effect.
"""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, RelatedImage, SourceRef
from app.services import llm, retriever

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
async def chat(req: ChatRequest) -> ChatResponse:
    """Non-streaming variant — simpler to call from scripts/tests."""
    started = time.perf_counter()
    try:
        hits = await retriever.retrieve(req.message, user_id=req.user_id)
        context = retriever.build_context(hits["text"])
        answer = await llm.generate(
            req.message, context, [t.model_dump() for t in req.history]
        )
    except RuntimeError as exc:
        # embeddings.py / llm.py raise RuntimeError for missing keys or
        # exhausted retries — surfaced as 503 (service unavailable), not 500,
        # since it's an upstream dependency issue, not a bug in our code.
        log.exception("chat failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return ChatResponse(
        answer=answer,
        sources=_sources(hits["text"]),
        images=[RelatedImage(**i) for i in retriever.format_images(hits["images"])],
        grounded=bool(context),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Server-Sent Events so the UI can type the answer out token by token.

    Retrieval happens up front (not streamed) because the frontend needs the
    full sources/images list before the first token arrives — that's the
    `meta` event. Only the LLM's answer itself streams token-by-token after.
    """
    hits = await retriever.retrieve(req.message, user_id=req.user_id)
    context = retriever.build_context(hits["text"])
    meta = {
        "sources": [s.model_dump() for s in _sources(hits["text"])],
        "images": retriever.format_images(hits["images"]),
        "grounded": bool(context),
    }

    async def event_stream():
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        try:
            async for token in llm.generate_stream(
                req.message, context, [t.model_dump() for t in req.history]
            ):
                yield f"event: token\ndata: {json.dumps({'t': token})}\n\n"
        except Exception as exc:  # noqa: BLE001 — surface any failure as an SSE event, not a crashed stream
            log.exception("stream failed")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n"
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
