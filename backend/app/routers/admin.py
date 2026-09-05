"""Admin panel API — login, ingestion (text / file / URL), browse, stats.

Matches the screens in report Figures 3.8, 3.9 and 3.11. Every route below
`Depends(require_admin)` is unreachable without a valid JWT (see security.py).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import settings
from app.models import (
    BrowseResponse,
    IngestResult,
    IngestTextRequest,
    IngestUrlRequest,
    LoginRequest,
    StatsResponse,
    TokenResponse,
)
from app.security import create_token, require_admin, verify_admin
from app.services import ingest, vectorstore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — generous for a notice/PDF, small enough to not stall the server


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest) -> TokenResponse:
    if not verify_admin(req.email, req.password):
        # Same 401 whether the email or the password was wrong — don't leak
        # which one, or an attacker learns valid admin emails for free.
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token, expires_in = create_token(req.email)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/stats", response_model=StatsResponse)
async def stats(_: str = Depends(require_admin)) -> StatsResponse:
    return StatsResponse(
        total_points=await vectorstore.count(),
        collection=settings.qdrant_collection,
        embedding_model=settings.embedding_model,
        llm_model=settings.cohere_model,
    )


@router.post("/ingest/text", response_model=IngestResult)
async def ingest_text_route(
    req: IngestTextRequest, _: str = Depends(require_admin)
) -> IngestResult:
    res = await ingest.ingest_text(req.text, source_name=req.source_name)
    return IngestResult(**res)


@router.post("/ingest/url", response_model=IngestResult)
async def ingest_url_route(req: IngestUrlRequest, _: str = Depends(require_admin)) -> IngestResult:
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http(s)://")
    try:
        res = await ingest.ingest_url(req.url)
    except Exception as exc:  # noqa: BLE001 — network/parse failures become a clean 502, not a stack trace
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc
    return IngestResult(**res)


@router.post("/ingest/file", response_model=IngestResult)
async def ingest_file_route(
    file: UploadFile = File(...), _: str = Depends(require_admin)
) -> IngestResult:
    blob = await file.read()
    if len(blob) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File larger than 25 MB")

    suffix = Path(file.filename or "upload").suffix.lower()

    if suffix == ".pdf":
        # PyMuPDF (ingest.ingest_pdf) needs a real file path, not an in-memory
        # blob, so we write it to a temp file and clean up in `finally` even
        # if ingestion raises.
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(blob)
            tmp_path = Path(tmp.name)
        try:
            res = await ingest.ingest_pdf(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        res["message"] = f"Ingested PDF {file.filename}"
        return IngestResult(**res)

    if suffix in {".txt", ".md", ".html", ".htm", ".csv"}:
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("latin-1", errors="ignore")
        res = await ingest.ingest_text(text, source_name=file.filename or "upload")
        res["message"] = f"Ingested {file.filename}"
        return IngestResult(**res)

    raise HTTPException(
        status_code=415, detail="Supported: .pdf, .txt, .md, .html, .csv"
    )


@router.get("/browse", response_model=BrowseResponse)
async def browse(
    limit: int = 250, offset: str | None = None, _: str = Depends(require_admin)
) -> BrowseResponse:
    return BrowseResponse(**await vectorstore.browse(limit=limit, offset=offset))


@router.delete("/doc/{doc_id}")
async def delete_doc(doc_id: str, _: str = Depends(require_admin)) -> dict:
    await vectorstore.delete_by_doc(doc_id)
    return {"deleted": doc_id}


@router.post("/reindex-folder", response_model=dict)
async def reindex_folder(_: str = Depends(require_admin)) -> dict:
    """Re-run the folder walk over data/{text_content,pdfs,images}."""
    return await ingest.ingest_directory()
