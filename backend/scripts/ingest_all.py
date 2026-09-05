"""Bulk-ingest everything under data/ into Qdrant.

This is the Day 3 milestone command — after the crawler has filled
data/{text_content,pdfs,images}, this is what turns that raw data into
searchable vectors so the chat UI can answer real CBIT questions.

Run after the crawler:
    python -m scripts.ingest_all
    python -m scripts.ingest_all --reset      # wipe the collection first
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import settings
from app.services import ingest, vectorstore

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("ingest")


async def run(reset: bool) -> None:
    client = vectorstore.get_client()

    if reset:
        # Used when you've changed CHUNK_SIZE/OVERLAP and want a clean
        # re-ingest rather than old and new chunks coexisting.
        try:
            await client.delete_collection(settings.qdrant_collection)
            log.info("Dropped collection %s", settings.qdrant_collection)
        except Exception as exc:  # noqa: BLE001 — nothing to drop on a fresh cluster, that's fine
            log.debug("nothing to drop: %s", exc)

    await vectorstore.ensure_collection()

    before = await vectorstore.count()
    stats = await ingest.ingest_directory()
    after = await vectorstore.count()

    log.info(
        "Ingested %d text files, %d PDFs, %d images -> %d new chunks (%d -> %d points)",
        stats["text"], stats["pdf"], stats["image"], stats["chunks"], before, after,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop the collection first")
    args = ap.parse_args()
    asyncio.run(run(args.reset))


if __name__ == "__main__":
    main()
