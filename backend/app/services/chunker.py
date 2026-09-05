"""Text cleaning and chunking — turns raw scraped/uploaded text into the
paragraph-sized pieces that actually get embedded.

Report §3.2.2 "Data Processing and Preparation": strip HTML tags and
whitespace, drop tiny fragments, then split into semantically meaningful
chunks before embedding.

Why chunk at all, instead of embedding a whole page? A single embedding
vector has to summarise everything fed into it — cram in a 5,000-word page
and the vector blurs into an average that matches nothing precisely. Small,
focused chunks give each fact its own precise vector.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.config import settings

MIN_CHARS = 40  # "minimum number of characters filter" — report §3.4.1
# Below this, a chunk is probably a stray nav-bar label or empty fragment,
# not real content worth embedding (embeddings aren't free — don't waste them).

_WS = re.compile(r"[ \t\xa0]+")       # runs of spaces/tabs/non-breaking-spaces
_NL = re.compile(r"\n{3,}")          # 3+ blank lines collapse to one blank line
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # non-printable control chars


def strip_html(raw: str) -> str:
    """Remove tags/scripts/styles and return readable text only."""
    if "<" not in raw:
        return raw  # already plain text — skip the parser entirely
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()  # these never contain user-facing content
    return soup.get_text("\n")


def clean_text(raw: str) -> str:
    """Normalise whitespace, drop control characters, collapse blank lines."""
    text = _CTRL.sub("", raw or "")
    text = _WS.sub(" ", text)
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)  # drop now-empty lines
    return _NL.sub("\n\n", text).strip()


def read_text_file(path) -> str:
    """UTF-8 with a latin-1 fallback — report §3.4.1.

    Some scraped/legacy files (old college site exports) aren't valid UTF-8.
    Rather than crash the whole ingest on one bad file, fall back to latin-1,
    which can decode any byte sequence (even if a few characters end up wrong).
    """
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def chunk_text(
    text: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[str]:
    """Split on paragraph boundaries, packing up to `chunk_size` characters.

    Overlap carries the tail of the previous chunk forward so a fact that
    straddles a chunk boundary is still retrievable whole in at least one
    chunk, instead of being cut in half and matching neither query.
    """
    chunk_size = chunk_size or settings.chunk_size
    overlap = overlap or settings.chunk_overlap

    text = clean_text(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        # Short document — one chunk is enough, as long as it clears MIN_CHARS.
        return [text] if len(text) >= MIN_CHARS else []

    paragraphs = [p for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    buf = ""

    for para in paragraphs:
        # A single paragraph longer than chunk_size (rare: a giant table dump)
        # can't be packed whole, so hard-split it on fixed-size windows.
        if len(para) > chunk_size:
            if buf:
                chunks.append(buf.strip())
                buf = ""
            for i in range(0, len(para), chunk_size - overlap):
                piece = para[i : i + chunk_size]
                if len(piece) >= MIN_CHARS:
                    chunks.append(piece.strip())
            continue

        if len(buf) + len(para) + 1 <= chunk_size:
            # Still room — keep packing paragraphs into the current chunk.
            buf = f"{buf}\n{para}" if buf else para
        else:
            # Buffer is full: close it out, then seed the next chunk with the
            # tail of this one (the overlap) before adding the new paragraph.
            chunks.append(buf.strip())
            tail = buf[-overlap:] if overlap else ""
            buf = f"{tail}\n{para}" if tail else para

    if buf.strip():
        chunks.append(buf.strip())

    return [c for c in chunks if len(c) >= MIN_CHARS]
