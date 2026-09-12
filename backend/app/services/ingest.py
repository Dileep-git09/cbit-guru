"""Ingestion pipeline — raw institutional data -> semantically searchable vectors.

Report §3.4.1 "Knowledge Base Construction using RAG Pipeline".

Handles four input kinds, all converging on the same `_store()` helper:
  * raw text pasted into the admin panel
  * PDF files          -> PyMuPDF, capped at MAX_PDF_CHARS (50,000)
  * images             -> represented in TEXT form (filename + caption/alt +
                          surrounding page text), which is what makes the
                          "multimodal" retrieval in report §3.5.3 work without
                          any vision model
  * a URL              -> fetched, tags stripped, then treated as text
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import fitz  # PyMuPDF — imported under its historical module name

from app.config import settings
from app.services import embeddings, vectorstore
from app.services.chunker import chunk_text, clean_text, read_text_file, strip_html

log = logging.getLogger(__name__)

SYSTEM_USER = "system"  # global chunks, visible to every visitor (see vectorstore._build_filter)


def _doc_id(*parts: str) -> str:
    # A stable, deterministic id derived from the source identity (not random)
    # — re-ingesting the exact same source produces the exact same doc_id,
    # which is what lets admin "delete this document" target the right chunks.
    return hashlib.sha1("::".join(parts).encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _store(
    chunks: list[str],
    *,
    doc_id: str,
    doc_type: str,
    user_id: str = SYSTEM_USER,
    file_name: str = "",
    url: str = "",
    system_scraper: bool = False,
    extra: dict[str, Any] | None = None,
) -> int:
    """Embed chunks and upsert with the metadata schema from report §3.4.2.

    Every ingestion path (text/pdf/image/url) funnels through here — this is
    the single place that builds the payload schema, so it can't drift
    between input types.
    """
    if not chunks:
        return 0

    vectors = await embeddings.embed_many(chunks)
    payloads = []
    for idx, chunk in enumerate(chunks):
        payload: dict[str, Any] = {
            "user_id": user_id,
            "doc_id": doc_id,
            "chunk_index": idx,        # this chunk's position within its source document
            "type": doc_type,
            "file_name": file_name,
            "url": url,
            "system_scraper": system_scraper,
            "ingested_at": _now(),
            "text": chunk,
        }
        if extra:
            payload.update(extra)
        payloads.append(payload)

    return await vectorstore.upsert_chunks(vectors, payloads)


# --------------------------------------------------------------------------- #
# Public ingestion entry points
# --------------------------------------------------------------------------- #

async def ingest_text(
    text: str,
    *,
    source_name: str = "manual-text",
    user_id: str = SYSTEM_USER,
    url: str = "",
    doc_type: str = "text",
    system_scraper: bool = False,
) -> dict[str, Any]:
    cleaned = clean_text(strip_html(text))
    chunks = chunk_text(cleaned)
    doc_id = _doc_id(source_name, url, cleaned[:200])
    written = await _store(
        chunks,
        doc_id=doc_id,
        doc_type=doc_type,
        user_id=user_id,
        file_name=source_name,
        url=url,
        system_scraper=system_scraper,
    )
    return {"doc_id": doc_id, "chunks": written, "chars": len(cleaned)}


async def ingest_pdf(
    path: Path | str,
    *,
    user_id: str = SYSTEM_USER,
    url: str = "",
    system_scraper: bool = False,
) -> dict[str, Any]:
    """PyMuPDF page-by-page extraction, capped at MAX_PDF_CHARS (report §3.4.1).

    Page-by-page (not the whole file at once) means a huge PDF stops early —
    at the cap — rather than either failing outright or silently ballooning
    the collection with one document's worth of chunks.
    """
    path = Path(path)
    pages: list[str] = []
    total = 0

    with fitz.open(path) as doc:
        for page in doc:
            txt = page.get_text("text")
            if not txt.strip():
                continue
            pages.append(txt)
            total += len(txt)
            if total >= settings.max_pdf_chars:
                log.info("PDF %s hit the %d-char cap", path.name, settings.max_pdf_chars)
                break

    body = clean_text("\n".join(pages))[: settings.max_pdf_chars]
    chunks = chunk_text(body)
    doc_id = _doc_id("pdf", path.name, str(len(body)))
    written = await _store(
        chunks,
        doc_id=doc_id,
        doc_type="pdf",
        user_id=user_id,
        file_name=path.name,
        url=url,
        system_scraper=system_scraper,
    )
    return {"doc_id": doc_id, "chunks": written, "chars": len(body)}


_SLUG = re.compile(r"[-_/\\.]+")


def image_to_text(
    file_name: str,
    caption: str = "",
    surrounding: str = "",
    page_title: str = "",
) -> str:
    """Build the TEXTUAL representation of an image — report §3.4.1.

    Filename tokens carry a surprising amount of signal on college sites
    ("boys_hostel_mess.jpg" -> "boys hostel mess"), so we expand the slug and
    concatenate it with the alt/caption text and nearby page copy. This text
    is what gets embedded — the image itself is never sent to any model.
    """
    stem = Path(file_name).stem
    words = " ".join(w for w in _SLUG.split(stem) if w and not w.isdigit())
    parts = [
        f"Image: {words}",
        f"Caption: {caption}" if caption else "",
        f"Page: {page_title}" if page_title else "",
        surrounding.strip()[:600],
    ]
    return clean_text("\n".join(p for p in parts if p))


async def ingest_image(
    *,
    file_name: str,
    image_url: str,
    caption: str = "",
    surrounding: str = "",
    page_title: str = "",
    tags: list[str] | None = None,
    user_id: str = SYSTEM_USER,
    system_scraper: bool = True,
) -> dict[str, Any]:
    body = image_to_text(file_name, caption, surrounding, page_title)
    if len(body) < 20:
        # Not enough signal (blank filename, no caption, no surrounding text)
        # to make this image findable by any query — skip it rather than
        # store a useless vector.
        return {"doc_id": "", "chunks": 0, "chars": 0}

    doc_id = _doc_id("image", image_url or file_name)
    written = await _store(
        [body],   # images are always a single chunk — the text is already short
        doc_id=doc_id,
        doc_type="image",
        user_id=user_id,
        file_name=file_name,
        url=image_url,
        system_scraper=system_scraper,
        extra={
            "image_url": image_url,
            "caption": caption or Path(file_name).stem.replace("_", " "),
            "tags": tags or [],
        },
    )
    return {"doc_id": doc_id, "chunks": written, "chars": len(body)}


async def ingest_url(url: str, *, user_id: str = SYSTEM_USER) -> dict[str, Any]:
    """Fetch a single page and ingest its readable text (admin panel's URL tab)."""
    timeout = aiohttp.ClientTimeout(total=45)
    headers = {"User-Agent": "CBITGuruBot/1.0 (+academic project)"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            raw = await resp.text(errors="ignore")

    return await ingest_text(
        raw,
        source_name=url,
        user_id=user_id,
        url=url,
        doc_type="web_scrape",
        system_scraper=True,
    )


async def ingest_directory(root: Path | str | None = None) -> dict[str, int]:
    """Walk data/{text_content,pdfs,images} and ingest everything.

    This is what `scripts/ingest_all.py` calls, and what admin's
    "reindex-folder" button re-runs — mirrors report §3.4.1's automatic
    folder walk.
    """
    root = Path(root) if root else settings.data_dir
    stats = {"text": 0, "pdf": 0, "image": 0, "chunks": 0}

    text_dir = root / "text_content"
    if text_dir.is_dir():
        for f in sorted(text_dir.glob("**/*")):
            if f.suffix.lower() not in {".txt", ".md", ".html", ".htm"} or not f.is_file():
                continue
            raw = read_text_file(f)
            res = await ingest_text(
                raw, source_name=f.name, doc_type="html_text", system_scraper=True
            )
            stats["text"] += 1
            stats["chunks"] += res["chunks"]
            log.info("text  %-45s %3d chunks", f.name, res["chunks"])

    pdf_dir = root / "pdfs"
    if pdf_dir.is_dir():
        for f in sorted(pdf_dir.glob("**/*.pdf")):
            try:
                res = await ingest_pdf(f, system_scraper=True)
            except Exception as exc:  # noqa: BLE001 — one bad PDF shouldn't kill the whole run
                log.warning("pdf %s failed: %s", f.name, exc)
                continue
            stats["pdf"] += 1
            stats["chunks"] += res["chunks"]
            log.info("pdf   %-45s %3d chunks", f.name, res["chunks"])

    # images/manifest.json is written by the scraper and carries captions.
    img_dir = root / "images"
    manifest = img_dir / "manifest.json"
    if manifest.is_file():
        for item in json.loads(read_text_file(manifest)):
            res = await ingest_image(
                file_name=item.get("file_name", ""),
                image_url=item.get("image_url", ""),
                caption=item.get("caption", ""),
                surrounding=item.get("surrounding", ""),
                page_title=item.get("page_title", ""),
                tags=item.get("tags", []),
            )
            if res["chunks"]:
                stats["image"] += 1
                stats["chunks"] += res["chunks"]

    return stats
