"""Generation layer — Cohere as the RAG generator.

Report §3.4.2 "Language Model (LLM)": answers come from `cohere_client.chat()`
combining a carefully drafted system prompt with the retrieved context.

Report §3.4.3: multilingual behaviour is achieved purely through prompt
engineering — the model detects the query language (English, Hindi, Telugu,
Hinglish, Tinglish) and replies in the same one. No translation step, no
per-language models. This is the answer to "why not a translation model?":
modern instruction-tuned LLMs are natively multilingual, so prompt
conditioning gives script-faithful replies at zero extra latency/cost.
"""
from __future__ import annotations

import asyncio
import logging
import random

import cohere

from app.config import settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BASE_DELAY = 0.3

# This prompt is doing three separate jobs at once, each mapped to a report
# section: grounding/anti-hallucination (§3.4.3), multilingual script-
# matching (§3.4.3), and tone for a non-technical audience (§3.4.2 frontend).
SYSTEM_PROMPT = """You are "CBIT Guru", the official AI assistant for Chaitanya \
Bharathi Institute of Technology (CBIT), Hyderabad.

GROUNDING RULES
- Answer ONLY from the CONTEXT block provided below. It is drawn from CBIT's \
official website, PDFs and notices.
- If the context does not contain the answer, say so plainly and point the user \
to the relevant CBIT office or page. NEVER invent facts, dates, fees, phone \
numbers or names.
- Prefer specific details (dates, names, departments, contact info) when the \
context has them. Cite the bracketed source numbers inline, like [2].

LANGUAGE RULES
- Detect the language AND script of the user's message and reply in exactly the \
same one.
- English -> English. Hindi in Devanagari -> Hindi in Devanagari. Telugu script \
-> Telugu script. Romanised Hindi ("Hinglish") -> reply in Hinglish. Romanised \
Telugu ("Tinglish") -> reply in Tinglish.
- Never switch scripts on the user.

STYLE
- Warm, concise, helpful. Use short bullet lists for multi-part answers.
- You are speaking to students, parents, faculty and visitors. Assume no \
technical knowledge.
"""

_client: cohere.AsyncClientV2 | None = None
_global_sem: asyncio.Semaphore | None = None


def _get_client() -> cohere.AsyncClientV2:
    global _client
    if _client is None:
        if not settings.cohere_api_key:
            raise RuntimeError("COHERE_API_KEY is not set — check backend/.env")
        _client = cohere.AsyncClientV2(api_key=settings.cohere_api_key)
    return _client


def _get_global_sem() -> asyncio.Semaphore:
    """Process-wide gate on in-flight Cohere calls — same reasoning as
    embeddings._get_global_sem(): many students asking at once should queue
    briefly, not all fire simultaneously and exhaust the shared quota."""
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(settings.cohere_max_concurrency)
    return _global_sem


def _is_rate_limit(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "too many requests" in text


def _build_messages(
    question: str,
    context: str,
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Report §4: contextual memory — recall the last few turns only. Capping
    # at 6 keeps every request's token cost bounded regardless of how long
    # the conversation has run, instead of resending the entire history.
    for turn in (history or [])[-6:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        content = (turn.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})

    # The retrieved context and the live question are bundled into one final
    # user turn, clearly labelled, so the model can't confuse "context" with
    # something the user said.
    grounded = (
        f"CONTEXT:\n{context}\n\n---\n\nQUESTION: {question}"
        if context
        else (
            "CONTEXT: (nothing relevant was found in the CBIT knowledge base)\n\n"
            f"QUESTION: {question}"
        )
    )
    messages.append({"role": "user", "content": grounded})
    return messages


async def generate(
    question: str,
    context: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """Single-shot grounded answer, with 429 backoff. Used by /api/chat."""
    client = _get_client()
    messages = _build_messages(question, context, history)
    last_exc: Exception | None = None

    for attempt in range(MAX_ATTEMPTS):
        try:
            async with _get_global_sem():
                resp = await client.chat(
                    model=settings.cohere_model,
                    messages=messages,
                    temperature=0.2,   # low temperature: favour grounded, repeatable answers over creativity
                )
            return "".join(
                item.text for item in resp.message.content if item.type == "text"
            ).strip()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == MAX_ATTEMPTS - 1 or not _is_rate_limit(exc):
                break
            delay = BASE_DELAY * (2**attempt) + random.uniform(0, 0.1)
            log.warning("Cohere rate-limited; sleeping %.2fs", delay)
            await asyncio.sleep(delay)

    raise RuntimeError(f"Generation failed: {last_exc}")


async def generate_stream(
    question: str,
    context: str,
    history: list[dict[str, str]] | None = None,
):
    """Token stream for the typing effect in the chat UI. Used by /api/chat/stream."""
    client = _get_client()
    messages = _build_messages(question, context, history)
    # Held for the WHOLE stream, not just the opening call — a streaming
    # response is one long-lived outbound connection, and it should count
    # against the concurrency cap for its entire duration.
    async with _get_global_sem():
        stream = client.chat_stream(
            model=settings.cohere_model, messages=messages, temperature=0.2
        )
        async for event in stream:
            if event.type == "content-delta":
                yield event.delta.message.content.text
