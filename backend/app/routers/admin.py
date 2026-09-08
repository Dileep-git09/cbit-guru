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
    AdminUserOut,
    BrowseResponse,
    ChangeOwnPasswordRequest,
    CreateAdminUserRequest,
    IngestResult,
    IngestTextRequest,
    IngestUrlRequest,
    LoginRequest,
    MeResponse,
    ResetPasswordRequest,
    StatsResponse,
    TokenResponse,
)
from app.security import CurrentAdmin, create_token, require_admin, require_superadmin, verify_admin
from app.services import cache, ingest, users, vectorstore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — generous for a notice/PDF, small enough to not stall the server


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest) -> TokenResponse:
    user = await verify_admin(req.email, req.password)
    if user is None:
        # Same 401 whether the email or the password was wrong — don't leak
        # which one, or an attacker learns valid admin emails for free.
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token, expires_in = create_token(user)
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=MeResponse)
async def me(current: CurrentAdmin = Depends(require_admin)) -> MeResponse:
    """The frontend calls this right after login to decide what to render —
    e.g. only a superadmin's panel gets the 'Admins' tab."""
    return MeResponse(**current)


@router.get("/users", response_model=list[AdminUserOut])
async def list_admin_users(_: CurrentAdmin = Depends(require_superadmin)) -> list[AdminUserOut]:
    return [AdminUserOut(**u) for u in await users.list_users()]


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def create_admin_user(
    req: CreateAdminUserRequest, _: CurrentAdmin = Depends(require_superadmin)
) -> AdminUserOut:
    try:
        user_id = await users.create_user(req.email, req.password, req.role)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return AdminUserOut(**await users.get_by_id(user_id))


@router.patch("/me/password")
async def change_own_password(
    req: ChangeOwnPasswordRequest, current: CurrentAdmin = Depends(require_admin)
) -> dict:
    # Requires the CURRENT password even though the caller is already
    # authenticated — a stolen-but-still-valid JWT shouldn't be enough on its
    # own to permanently lock the real owner out of their account.
    if await users.verify_credentials(current["email"], req.current_password) is None:
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    await users.update_password(current["id"], req.new_password)
    return {"message": "Password updated"}


@router.patch("/users/{user_id}/password")
async def reset_user_password(
    user_id: str, req: ResetPasswordRequest, _: CurrentAdmin = Depends(require_superadmin)
) -> dict:
    """Super-admin override: no current password needed, since the point is
    the target user may have forgotten theirs."""
    target = await users.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    await users.update_password(user_id, req.new_password)
    return {"message": f"Password reset for {target['email']}"}


@router.delete("/users/{user_id}")
async def delete_admin_user(
    user_id: str, _: CurrentAdmin = Depends(require_superadmin)
) -> dict:
    target = await users.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target["role"] == "superadmin" and await users.count_superadmins() <= 1:
        # Without this guard, a single mistaken delete could permanently
        # lock every admin out of account management — nobody left with the
        # role required to create a replacement superadmin.
        raise HTTPException(status_code=400, detail="Cannot delete the last remaining super admin")
    await users.delete_user(user_id)
    return {"deleted": user_id}


@router.get("/stats", response_model=StatsResponse)
async def stats(_: CurrentAdmin = Depends(require_admin)) -> StatsResponse:
    cache_stats = await cache.stats()
    return StatsResponse(
        total_points=await vectorstore.count(),
        collection=settings.qdrant_collection,
        embedding_model=settings.embedding_model,
        llm_model=settings.cohere_model,
        cache_exact_entries=cache_stats["exact_entries"],
        cache_semantic_entries=cache_stats["semantic_entries"],
        cache_backend=cache_stats["exact_backend"],
    )


@router.post("/ingest/text", response_model=IngestResult)
async def ingest_text_route(
    req: IngestTextRequest, _: CurrentAdmin = Depends(require_admin)
) -> IngestResult:
    res = await ingest.ingest_text(req.text, source_name=req.source_name)
    return IngestResult(**res)


@router.post("/ingest/url", response_model=IngestResult)
async def ingest_url_route(req: IngestUrlRequest, _: CurrentAdmin = Depends(require_admin)) -> IngestResult:
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL must start with http(s)://")
    try:
        res = await ingest.ingest_url(req.url)
    except Exception as exc:  # noqa: BLE001 — network/parse failures become a clean 502, not a stack trace
        raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}") from exc
    return IngestResult(**res)


@router.post("/ingest/file", response_model=IngestResult)
async def ingest_file_route(
    file: UploadFile = File(...), _: CurrentAdmin = Depends(require_admin)
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
    limit: int = 250, offset: str | None = None, _: CurrentAdmin = Depends(require_admin)
) -> BrowseResponse:
    return BrowseResponse(**await vectorstore.browse(limit=limit, offset=offset))


@router.delete("/doc/{doc_id}")
async def delete_doc(doc_id: str, _: CurrentAdmin = Depends(require_admin)) -> dict:
    await vectorstore.delete_by_doc(doc_id)
    return {"deleted": doc_id}


@router.post("/reindex-folder", response_model=dict)
async def reindex_folder(_: CurrentAdmin = Depends(require_admin)) -> dict:
    """Re-run the folder walk over data/{text_content,pdfs,images}."""
    return await ingest.ingest_directory()
