"""One-time bootstrap for scripts/refresh_knowledge_base.py.

This project's very first real ingest (ROADMAP Day 3) went in through
ingest_directory()/ingest_all.py, which has no memory of what it ingested —
no crawl_state.json exists yet. Without this script, the FIRST real run of
refresh_knowledge_base.py would see every already-ingested source as "new"
(nothing in the state file matches it) and duplicate the entire knowledge
base in Qdrant right alongside what's already there.

What this does instead: scroll every point already in Qdrant, recover each
document's doc_id from its payload, match it back to the corresponding
local file in data/ by the same key scheme refresh_knowledge_base.py uses,
and write crawl_state.json with the EXISTING doc_id plus a hash of the
CURRENT local content — so the next refresh run correctly sees "unchanged"
instead of re-ingesting everything.

Run exactly once, before the first refresh_knowledge_base.py run, on a
collection that was populated by ingest_directory()/ingest_all.py:
    python -m scripts.bootstrap_crawl_state
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.services.vectorstore import get_client
from scripts.refresh_knowledge_base import STATE_FILE, _text_key

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("bootstrap_crawl_state")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _scan_qdrant() -> dict[str, str]:
    """key -> doc_id, for every scraper-ingested document already in Qdrant."""
    client = get_client()
    key_to_doc_id: dict[str, str] = {}
    offset = None
    scanned = 0

    while True:
        points, offset = await client.scroll(
            collection_name=settings.qdrant_collection,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        scanned += len(points)
        for p in points:
            payload = p.payload or {}
            if not payload.get("system_scraper"):
                continue  # admin-uploaded content isn't this pipeline's concern
            doc_type = payload.get("type", "")
            file_name = payload.get("file_name", "")
            url = payload.get("url", "")

            # web_scrape (admin panel's single-URL ingest) is deliberately
            # left out here — it's an ad hoc admin action, not part of the
            # bulk scraper/text_content directory this pipeline manages.
            if doc_type == "html_text" and file_name:
                key = f"text:{file_name}"
            elif doc_type == "pdf" and file_name:
                key = f"pdf:{file_name}"
            elif doc_type == "image" and url:
                key = f"image:{url}"
            else:
                continue
            key_to_doc_id[key] = payload.get("doc_id", "")

        if offset is None:
            break

    log.info("scanned %d existing points -> %d distinct scraper-managed documents", scanned, len(key_to_doc_id))
    return key_to_doc_id


def _local_text_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    text_dir = root / "text_content"
    if not text_dir.is_dir():
        return out
    for f in sorted(text_dir.glob("**/*")):
        if f.suffix.lower() not in {".txt", ".md", ".html", ".htm"} or not f.is_file():
            continue
        raw_bytes = f.read_bytes()
        out[_text_key(f)] = _sha256(raw_bytes)
    return out


def _local_pdf_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    pdf_dir = root / "pdfs"
    if not pdf_dir.is_dir():
        return out
    for f in sorted(pdf_dir.glob("**/*.pdf")):
        out[f"pdf:{f.name}"] = _sha256(f.read_bytes())
    return out


def _local_image_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    manifest = root / "images" / "manifest.json"
    if not manifest.is_file():
        return out
    for item in json.loads(manifest.read_text(encoding="utf-8")):
        image_url = item.get("image_url", "")
        if not image_url:
            continue
        fingerprint = json.dumps(item, sort_keys=True).encode("utf-8")
        out[f"image:{image_url}"] = _sha256(fingerprint)
    return out


async def run(root: Path) -> None:
    qdrant_keys = await _scan_qdrant()
    local_hashes: dict[str, tuple[str, str]] = {}  # key -> (hash, kind)
    for key, h in _local_text_hashes(root).items():
        local_hashes[key] = (h, "text")
    for key, h in _local_pdf_hashes(root).items():
        local_hashes[key] = (h, "pdf")
    for key, h in _local_image_hashes(root).items():
        local_hashes[key] = (h, "image")

    state: dict[str, dict[str, Any]] = {}
    matched = 0
    for key, (content_hash, kind) in local_hashes.items():
        doc_id = qdrant_keys.get(key)
        if doc_id:
            state[key] = {"doc_id": doc_id, "content_sha256": content_hash, "kind": kind}
            matched += 1

    local_only = len(local_hashes) - matched
    qdrant_only = len(qdrant_keys) - matched

    state_path = root / STATE_FILE
    if state_path.is_file():
        log.error(
            "%s already exists — refusing to overwrite. Delete it first if you really mean to re-bootstrap.",
            state_path,
        )
        sys.exit(1)

    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")

    print("=" * 60)
    print("Bootstrap summary")
    print(f"matched (adopted existing doc_id) : {matched}")
    print(f"local-only (will be ingested as new on the next refresh run) : {local_only}")
    print(f"qdrant-only (already in Qdrant, no local file — left untouched) : {qdrant_only}")
    print(f"wrote {state_path}")
    print("=" * 60)


def main() -> None:
    asyncio.run(run(settings.data_dir))


if __name__ == "__main__":
    main()
