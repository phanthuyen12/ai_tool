import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import Background, MediaAsset, User
from app.schemas.studio import BackgroundCreate, BackgroundResponse, BackgroundUpdate
from app.services.media_storage.manager import MediaStorageService
from app.services.serializers import serialize_background
from app.services.vision_caption.captioner import generate_image_caption

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backgrounds", tags=["backgrounds"])


@router.get("", response_model=list[BackgroundResponse])
def list_backgrounds(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    rows = db.scalars(
        select(Background)
        .where(Background.owner_id == current_user.id)
        .order_by(Background.created_at.desc())
    ).all()
    return [serialize_background(db, bg, storage) for bg in rows]


@router.post("", response_model=BackgroundResponse, status_code=status.HTTP_201_CREATED)
def create_background(
    payload: BackgroundCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    ma = db.get(MediaAsset, payload.media_asset_id) if payload.media_asset_id else None

    cld_url = payload.cloudinary_url
    if ma and not cld_url and ma.storage_backend == "cloudinary" and ma.public_url:
        cld_url = ma.public_url

    final_description = payload.description.strip()
    if not final_description and ma and storage.cfg and storage.cfg.caption_llm_enabled:
        try:
            caption = generate_image_caption(
                storage.read_asset_bytes(ma), storage.cfg, subject_hint="studio background environment"
            )
            if caption:
                final_description = caption
        except Exception as exc:
            logger.warning("Caption LLM failed for background asset %s: %s", payload.media_asset_id, exc)

    bg = Background(
        owner_id=current_user.id,
        name=payload.name.strip(),
        description=final_description,
        style=payload.style.strip(),
        media_asset_id=payload.media_asset_id,
        cloudinary_url=cld_url,
        preservation_rules_json=payload.preservation_rules_json.model_dump(),
        default_character_id=payload.default_character_id,
    )
    db.add(bg)
    db.commit()
    db.refresh(bg)
    return serialize_background(db, bg, storage)


@router.get("/{background_id}", response_model=BackgroundResponse)
def get_background(
    background_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bg = db.get(Background, background_id)
    if not bg or bg.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Background not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_background(db, bg, storage)


@router.patch("/{background_id}", response_model=BackgroundResponse)
def update_background(
    background_id: str,
    payload: BackgroundUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bg = db.get(Background, background_id)
    if not bg or bg.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Background not found")
    data = payload.model_dump(exclude_unset=True)
    if "preservation_rules_json" in data and payload.preservation_rules_json is not None:
        data["preservation_rules_json"] = payload.preservation_rules_json.model_dump()
    for k, v in data.items():
        setattr(bg, k, v)
    db.commit()
    db.refresh(bg)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_background(db, bg, storage)


@router.delete("/{background_id}", status_code=204)
def delete_background(
    background_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    bg = db.get(Background, background_id)
    if not bg or bg.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Background not found")
    db.delete(bg)
    db.commit()
    return None

