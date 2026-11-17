import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import (
    Character,
    CharacterReference,
    CharacterRule,
    MediaAsset,
    User,
)
from app.schemas.studio import (
    CharacterCreate,
    CharacterReferenceCreate,
    CharacterResponse,
    CharacterRulePayload,
    CharacterRuleResponse,
    CharacterUpdate,
)
from app.services.media_storage.manager import MediaStorageService
from app.services.serializers import serialize_character, serialize_character_rule
from app.services.vision_caption.captioner import generate_image_caption

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("/rules/presets", response_model=list[CharacterRuleResponse])
def list_rule_presets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    presets = db.scalars(
        select(CharacterRule)
        .where(CharacterRule.owner_id == current_user.id, CharacterRule.is_preset.is_(True))
        .order_by(CharacterRule.created_at.desc())
    ).all()
    return [serialize_character_rule(p) for p in presets]


@router.post("/rules/presets", response_model=CharacterRuleResponse, status_code=201)
def create_rule_preset(
    payload: CharacterRulePayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = payload.model_dump()
    data["is_preset"] = True
    data["preset_name"] = payload.preset_name or "Custom Rule Preset"
    preset = CharacterRule(owner_id=current_user.id, character_id=None, **data)
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return serialize_character_rule(preset)


@router.get("", response_model=list[CharacterResponse])
def list_characters(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    rows = db.scalars(
        select(Character)
        .where(Character.owner_id == current_user.id)
        .order_by(Character.is_default.desc(), Character.created_at.desc())
    ).all()
    return [serialize_character(db, c, storage) for c in rows]


@router.post("", response_model=CharacterResponse, status_code=status.HTTP_201_CREATED)
def create_character(
    payload: CharacterCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.is_default:
        existing_defaults = db.scalars(
            select(Character).where(
                Character.owner_id == current_user.id, Character.is_default.is_(True)
            )
        ).all()
        for ed in existing_defaults:
            ed.is_default = False

    primary_id = payload.primary_image_asset_id or (
        payload.reference_asset_ids[0] if payload.reference_asset_ids else None
    )

    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    final_description = payload.description.strip()
    if not final_description and primary_id and storage.cfg and storage.cfg.caption_llm_enabled:
        if primary_asset := db.get(MediaAsset, primary_id):
            try:
                caption = generate_image_caption(
                    storage.read_asset_bytes(primary_asset), storage.cfg, subject_hint="presenter character"
                )
                if caption:
                    final_description = caption
            except Exception as exc:
                logger.warning("Caption LLM failed for character primary asset %s: %s", primary_id, exc)

    char = Character(
        owner_id=current_user.id,
        name=payload.name.strip(),
        description=final_description,
        age_appearance=payload.age_appearance,
        voice_style=payload.voice_style.strip(),
        role_title=payload.role_title.strip(),
        default_screen_time_seconds=payload.default_screen_time_seconds,
        primary_image_asset_id=primary_id,
        default_background_id=payload.default_background_id,
        is_default=payload.is_default,
    )
    db.add(char)
    db.flush()

    seen_refs: set[str] = set()
    ref_ids = ([primary_id] if primary_id else []) + list(payload.reference_asset_ids)
    order = 0
    for asset_id in ref_ids:
        if not asset_id or asset_id in seen_refs:
            continue
        seen_refs.add(asset_id)
        db.add(
            CharacterReference(
                character_id=char.id,
                media_asset_id=asset_id,
                angle_label="front" if order == 0 else "angle",
                is_primary=(asset_id == primary_id),
                sort_order=order,
            )
        )
        order += 1

    rule_data = (payload.rule or CharacterRulePayload()).model_dump()
    rule_data["is_preset"] = False
    db.add(CharacterRule(owner_id=current_user.id, character_id=char.id, **rule_data))

    db.commit()
    db.refresh(char)
    return serialize_character(db, char, storage)


@router.get("/{character_id}", response_model=CharacterResponse)
def get_character(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = db.get(Character, character_id)
    if not char or char.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Character not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_character(db, char, storage)


@router.patch("/{character_id}", response_model=CharacterResponse)
def update_character(
    character_id: str,
    payload: CharacterUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = db.get(Character, character_id)
    if not char or char.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Character not found")

    if payload.is_default is True:
        existing_defaults = db.scalars(
            select(Character).where(
                Character.owner_id == current_user.id, Character.is_default.is_(True)
            )
        ).all()
        for ed in existing_defaults:
            ed.is_default = False

    update_data = payload.model_dump(exclude_unset=True, exclude={"rule"})
    for field, value in update_data.items():
        setattr(char, field, value)

    if payload.rule is not None:
        rule_row = db.scalars(
            select(CharacterRule).where(CharacterRule.character_id == char.id)
        ).first()
        r_data = payload.rule.model_dump()
        r_data["is_preset"] = False
        if rule_row:
            for k, v in r_data.items():
                setattr(rule_row, k, v)
        else:
            db.add(CharacterRule(owner_id=current_user.id, character_id=char.id, **r_data))

    db.commit()
    db.refresh(char)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_character(db, char, storage)


@router.post("/{character_id}/references", response_model=CharacterResponse)
def add_character_reference(
    character_id: str,
    payload: CharacterReferenceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = db.get(Character, character_id)
    if not char or char.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Character not found")

    if payload.is_primary:
        for ref in char.references:
            ref.is_primary = False
        char.primary_image_asset_id = payload.media_asset_id

    next_order = max((r.sort_order for r in char.references), default=-1) + 1
    db.add(
        CharacterReference(
            character_id=char.id,
            media_asset_id=payload.media_asset_id,
            angle_label=payload.angle_label,
            is_primary=payload.is_primary or (char.primary_image_asset_id is None),
            sort_order=next_order,
        )
    )
    if not char.primary_image_asset_id:
        char.primary_image_asset_id = payload.media_asset_id
    db.commit()
    db.refresh(char)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_character(db, char, storage)


@router.delete("/{character_id}/references/{ref_id}", response_model=CharacterResponse)
def remove_character_reference(
    character_id: str,
    ref_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = db.get(Character, character_id)
    if not char or char.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Character not found")
    ref = db.get(CharacterReference, ref_id)
    if not ref or ref.character_id != char.id:
        raise HTTPException(status_code=404, detail="Reference not found")
    was_primary = ref.is_primary
    db.delete(ref)
    db.flush()
    if was_primary:
        remaining = db.scalars(
            select(CharacterReference)
            .where(CharacterReference.character_id == char.id)
            .order_by(CharacterReference.sort_order.asc())
        ).first()
        char.primary_image_asset_id = remaining.media_asset_id if remaining else None
        if remaining:
            remaining.is_primary = True
    db.commit()
    db.refresh(char)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_character(db, char, storage)


@router.delete("/{character_id}", status_code=204)
def delete_character(
    character_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    char = db.get(Character, character_id)
    if not char or char.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Character not found")
    db.delete(char)
    db.commit()
    return None

