from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    validate_external_url,
    validate_media_bytes,
    verify_media_token,
)
from app.models.entities import MediaAsset, User
from app.schemas.studio import ImportUrlRequest, MediaAssetResponse
from app.services.media_storage.manager import MediaStorageService
from app.services.serializers import serialize_media_asset

router = APIRouter(prefix="/media", tags=["media"])


@router.get("", response_model=list[MediaAssetResponse])
def list_media_assets(
    asset_kind: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    stmt = select(MediaAsset).where(MediaAsset.owner_id == current_user.id)
    if asset_kind:
        stmt = stmt.where(MediaAsset.asset_kind == asset_kind)
    if project_id:
        stmt = stmt.where(MediaAsset.project_id == project_id)
    rows = db.scalars(stmt.order_by(MediaAsset.created_at.desc()).limit(200)).all()
    return [serialize_media_asset(row, storage) for row in rows]


@router.post("/upload", response_model=MediaAssetResponse)
async def upload_media(
    file: UploadFile = File(...),
    asset_kind: str = Form(default="image"),
    project_id: Optional[str] = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    mime = validate_media_bytes(raw, asset_kind)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    asset = storage.save_bytes(
        db,
        owner_id=current_user.id,
        data=raw,
        original_filename=file.filename or f"upload.{mime.split('/')[-1]}",
        mime_type=mime,
        asset_kind=asset_kind,
        project_id=project_id,
    )
    db.commit()
    db.refresh(asset)
    return serialize_media_asset(asset, storage)


@router.post("/import-url", response_model=MediaAssetResponse)
def import_media_url(
    payload: ImportUrlRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    safe_url = validate_external_url(payload.url)
    max_bytes = 20 * 1024 * 1024 if payload.asset_kind == "image" else 250 * 1024 * 1024
    try:
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            resp = client.get(safe_url)
            if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                redirected = validate_external_url(resp.headers["location"])
                resp = client.get(redirected)
            resp.raise_for_status()
            if len(resp.content) > max_bytes:
                raise HTTPException(status_code=413, detail="Remote file exceeds max allowed size")
            data = resp.content
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed fetching remote media URL: {exc}") from exc

    mime = validate_media_bytes(data, payload.asset_kind)
    parsed = urlparse(safe_url)
    filename = payload.filename or Path(parsed.path).name or f"imported.{mime.split('/')[-1]}"
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    asset = storage.save_bytes(
        db,
        owner_id=current_user.id,
        data=data,
        original_filename=filename,
        mime_type=mime,
        asset_kind=payload.asset_kind,
        project_id=payload.project_id,
        extra_metadata={"source_url": safe_url},
    )
    db.commit()
    db.refresh(asset)
    return serialize_media_asset(asset, storage)


@router.get("/files/{asset_id}")
def serve_signed_local_media(
    asset_id: str,
    request: Request,
    sig: str = Query(...),
    exp: int = Query(...),
    download: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Serve local persistent media via signed HMAC URL with HTTP Range support for video seeking."""
    if not verify_media_token(asset_id, sig, exp):
        raise HTTPException(status_code=403, detail="Invalid or expired media signature")

    asset = db.get(MediaAsset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")

    storage = MediaStorageService()
    if asset.storage_backend != "local":
        raise HTTPException(status_code=400, detail="Asset is hosted on external cloud storage")

    file_path = storage.local.resolve_path(asset.storage_key)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing from disk")

    file_size = file_path.stat().st_size
    disp = "attachment" if download else "inline"
    base_headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'{disp}; filename="{asset.original_filename}"',
        "Cache-Control": "private, max-age=3600",
    }

    range_header = request.headers.get("range")
    if range_header and range_header.startswith("bytes="):
        byte_spec = range_header.replace("bytes=", "").split(",")[0].strip()
        start_s, end_s = byte_spec.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else file_size - 1
        end = min(end, file_size - 1)
        if start >= file_size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        length = end - start + 1

        def _iter_range():
            with file_path.open("rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(262144, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        headers = {
            **base_headers,
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(length),
        }
        return StreamingResponse(
            _iter_range(), status_code=206, media_type=asset.mime_type, headers=headers
        )

    def _iter_full():
        with file_path.open("rb") as fh:
            while chunk := fh.read(262144):
                yield chunk

    return StreamingResponse(
        _iter_full(),
        media_type=asset.mime_type,
        headers={**base_headers, "Content-Length": str(file_size)},
    )


@router.delete("/{asset_id}", status_code=204)
def delete_media_asset(
    asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    asset = db.get(MediaAsset, asset_id)
    if not asset or asset.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Media asset not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    if asset.storage_backend == "local":
        storage.local.delete(asset.storage_key)
    db.delete(asset)
    db.commit()
    return None

