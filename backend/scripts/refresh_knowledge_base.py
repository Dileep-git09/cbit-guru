"""Incremental knowledge-base refresh — implements docs/FUTURE_SCOPE_AUTO_REFRESH.md.

Turns ingestion from "run once against an empty collection" into something
safe to run on a schedule: after a fresh `python -m scraper.crawl`, this
script diffs the new data/ against what was ingested last time and touches
Qdrant only for what's actually new, changed, or removed.

Why a diff is necessary at all (not just re-running ingest_directory()):
  * Qdrant point IDs are random UUIDs (vectorstore.upsert_chunks) — re-running
    ingestion blindly duplicates every unchanged chunk instead of updating it.
  * Scraped-page doc_ids are content-derived (ingest._doc_id embeds the first
    200 chars of text) — a page's doc_id changes when its content does, so
    there is no way to know what to delete without having recorded it.
This script's crawl_state.json is exactly that record: url -> {doc_id,
content_sha256}, so a changed page can be deleted by its OLD doc_id before
being re-ingested under its new one.

Run after a real scrape:
    python -m scraper.crawl
    python -m scripts.refresh_knowledge_base            # writes to Qdrant
    python -m scripts.refresh_knowledge_base --dry-run   # reports the diff only
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.config import settings
from app.services import ingest, vectorstore

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

log = logging.getLogger("refresh_kb")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")

STATE_FILE = "crawl_state.json"

# If a fresh crawl comes back with fewer than this fraction of last run's
# page count, something's wrong with the crawl itself (site down, network
# blip serving error pages) — treat it as a bad run, not "everything else
# got deleted", and abort before touching Qdrant. See design doc's
# "politeness and safety" section.
MIN_SURVIVING_FRACTION = 0.5


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )


def _text_key(path: Path) -> str:
    # Keyed on filename, not the page's actual URL. Two reasons: (1) it's
    # what ingest_directory() has always stored in payload.file_name for
    # html_text docs (payload.url was empty until this same change added
    # it — see ingest.py) — bootstrapping against the existing production
    # collection needs a key that already-ingested points actually carry.
    # (2) crawl.py's safe_name(url) makes the filename a stable, 1:1,
    # already-deterministic function of the URL anyway, so nothing is lost
    # by keying on it directly instead of re-extracting the URL.
    return f"text:{path.name}"


async def _sync_text(
    root: Path, state: dict[str, dict[str, Any]], dry_run: bool
) -> tuple[dict[str, list[str]], set[str]]:
    seen: set[str] = set()
    changes = {"new": [], "changed": [], "unchanged": []}

    text_dir = root / "text_content"
    if not text_dir.is_dir():
        return changes, seen

    for f in sorted(text_dir.glob("**/*")):
        if f.suffix.lower() not in {".txt", ".md", ".html", ".htm"} or not f.is_file():
            continue
        raw_bytes = f.read_bytes()
        raw = raw_bytes.decode("utf-8", errors="ignore")
        key = _text_key(f)
        seen.add(key)
        content_hash = _sha256(raw_bytes)
        first_line = raw.split("\n", 1)[0]
        page_url = first_line[len("URL: ") :].strip() if first_line.startswith("URL: ") else ""

        prev = state.get(key)
        if prev is None:
            changes["new"].append(key)
            if not dry_run:
                res = await ingest.ingest_text(
                    raw, source_name=f.name, url=page_url, doc_type="html_text", system_scraper=True
                )
                # Only record state when something was actually written —
                # otherwise a source that legitimately embeds to 0 chunks
                # would be remembered as "done" with a doc_id nothing backs.
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "text"}
        elif prev["content_sha256"] != content_hash:
            changes["changed"].append(key)
            if not dry_run:
                await vectorstore.delete_by_doc(prev["doc_id"])
                res = await ingest.ingest_text(
                    raw, source_name=f.name, url=page_url, doc_type="html_text", system_scraper=True
                )
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "text"}
                else:
                    state.pop(key, None)
        else:
            changes["unchanged"].append(key)

    return changes, seen


async def _sync_pdfs(
    root: Path, state: dict[str, dict[str, Any]], dry_run: bool
) -> tuple[dict[str, list[str]], set[str]]:
    seen: set[str] = set()
    changes = {"new": [], "changed": [], "unchanged": []}

    pdf_dir = root / "pdfs"
    if not pdf_dir.is_dir():
        return changes, seen

    for f in sorted(pdf_dir.glob("**/*.pdf")):
        key = f"pdf:{f.name}"
        seen.add(key)
        content_hash = _sha256(f.read_bytes())

        prev = state.get(key)
        if prev is None:
            changes["new"].append(key)
            if not dry_run:
                try:
                    res = await ingest.ingest_pdf(f, system_scraper=True)
                except Exception as exc:  # noqa: BLE001 — one bad PDF shouldn't kill the run
                    log.warning("pdf %s failed: %s", f.name, exc)
                    continue
                # Scanned/image-only PDFs extract to 0 chunks (no OCR text
                # layer) — don't record a doc_id nothing backs, or this
                # source would wrongly read as "already handled" forever.
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "pdf"}
        elif prev["content_sha256"] != content_hash:
            changes["changed"].append(key)
            if not dry_run:
                await vectorstore.delete_by_doc(prev["doc_id"])
                try:
                    res = await ingest.ingest_pdf(f, system_scraper=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pdf %s failed: %s", f.name, exc)
                    continue
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "pdf"}
                else:
                    state.pop(key, None)
        else:
            changes["unchanged"].append(key)

    return changes, seen


async def _sync_images(
    root: Path, state: dict[str, dict[str, Any]], dry_run: bool
) -> tuple[dict[str, list[str]], set[str]]:
    seen: set[str] = set()
    changes = {"new": [], "changed": [], "unchanged": []}

    manifest = root / "images" / "manifest.json"
    if not manifest.is_file():
        return changes, seen

    for item in json.loads(manifest.read_text(encoding="utf-8")):
        image_url = item.get("image_url", "")
        if not image_url:
            continue
        key = f"image:{image_url}"
        seen.add(key)
        # The image's bytes aren't necessarily downloaded (download_images
        # can be off) — what actually feeds the embedding is this metadata,
        # so that's what defines "changed" here, not the pixels.
        fingerprint = json.dumps(item, sort_keys=True).encode("utf-8")
        content_hash = _sha256(fingerprint)

        prev = state.get(key)

        async def _ingest_one() -> dict[str, Any]:
            return await ingest.ingest_image(
                file_name=item.get("file_name", ""),
                image_url=image_url,
                caption=item.get("caption", ""),
                surrounding=item.get("surrounding", ""),
                page_title=item.get("page_title", ""),
                tags=item.get("tags", []),
            )

        if prev is None:
            changes["new"].append(key)
            if not dry_run:
                res = await _ingest_one()
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "image"}
        elif prev["content_sha256"] != content_hash:
            changes["changed"].append(key)
            if not dry_run:
                await vectorstore.delete_by_doc(prev["doc_id"])
                res = await _ingest_one()
                if res["chunks"]:
                    state[key] = {"doc_id": res["doc_id"], "content_sha256": content_hash, "kind": "image"}
        else:
            changes["unchanged"].append(key)

    return changes, seen


async def run(root: Path, dry_run: bool) -> int:
    state_path = root / STATE_FILE
    state = _load_state(state_path)
    prev_count = len(state)

    text_changes, text_seen = await _sync_text(root, state, dry_run)
    pdf_changes, pdf_seen = await _sync_pdfs(root, state, dry_run)
    image_changes, image_seen = await _sync_images(root, state, dry_run)

    all_seen = text_seen | pdf_seen | image_seen
    survival_ok = prev_count == 0 or len(all_seen) >= prev_count * MIN_SURVIVING_FRACTION

    removed: list[str] = []
    if not survival_ok:
        log.error(
            "This crawl only produced %d of %d previously-known sources (< %.0f%%) — "
            "treating this as a bad/partial scrape, NOT deleting anything. "
            "Check the scraper output before re-running.",
            len(all_seen), prev_count, MIN_SURVIVING_FRACTION * 100,
        )
    else:
        removed = [key for key in state if key not in all_seen]
        for key in removed:
            if not dry_run:
                await vectorstore.delete_by_doc(state[key]["doc_id"])
            del state[key]

    if not dry_run and survival_ok:
        _save_state(state_path, state)

    def _summarize(name: str, changes: dict[str, list[str]]) -> None:
        log.info(
            "%-6s new=%-3d changed=%-3d unchanged=%-3d",
            name, len(changes["new"]), len(changes["changed"]), len(changes["unchanged"]),
        )

    print("=" * 60)
    print(f"{'DRY RUN — ' if dry_run else ''}Knowledge base refresh summary")
    _summarize("text", text_changes)
    _summarize("pdf", pdf_changes)
    _summarize("image", image_changes)
    if survival_ok:
        print(f"removed  {len(removed)} source(s) no longer on the site")
    else:
        print("removed  SKIPPED (failed survival check)")
    print("=" * 60)

    return 0 if survival_ok else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(settings.data_dir))
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Report what would change without writing to Qdrant or crawl_state.json",
    )
    args = ap.parse_args()
    exit_code = asyncio.run(run(Path(args.root), args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
