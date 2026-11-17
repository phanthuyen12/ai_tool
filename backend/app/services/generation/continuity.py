from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import (
    Background,
    Character,
    CharacterReference,
    MediaAsset,
    Project,
    Scene,
    SceneAsset,
)
from app.services.media_storage.manager import MediaStorageService

EXPECTED_RATIOS = {
    "9:16": 9.0 / 16.0,
    "16:9": 16.0 / 9.0,
    "1:1": 1.0,
}


class ContinuityGateError(Exception):
    """Raised when Scene N tries to generate before Master Image is ready/approved."""


def get_active_scene_image_asset(db: Session, scene_id: str) -> Optional[SceneAsset]:
    return db.scalars(
        select(SceneAsset)
        .where(
            SceneAsset.scene_id == scene_id,
            SceneAsset.asset_role.in_(["master_image", "continuity_image"]),
            SceneAsset.is_active.is_(True),
        )
        .order_by(SceneAsset.version.desc())
    ).first()


def get_active_scene_video_asset(db: Session, scene_id: str) -> Optional[SceneAsset]:
    return db.scalars(
        select(SceneAsset)
        .where(
            SceneAsset.scene_id == scene_id,
            SceneAsset.asset_role == "scene_video",
            SceneAsset.is_active.is_(True),
        )
        .order_by(SceneAsset.version.desc())
    ).first()


def assemble_video_continuity_references(
    db: Session, *, project: Project, scene: Scene, current_scene_image: SceneAsset
) -> list[tuple[MediaAsset, str]]:
    """Build a small, ordered visual anchor set for reference-to-video models."""
    refs: list[tuple[MediaAsset, str]] = []
    seen: set[str] = set()

    def add(media_id: Optional[str], label: str) -> None:
        if not media_id or media_id in seen or len(refs) >= 3:
            return
        media = db.get(MediaAsset, media_id)
        if media:
            refs.append((media, label))
            seen.add(media_id)

    add(current_scene_image.media_asset_id, f"current_scene_{scene.scene_index:02d}")

    master_scene = get_master_scene(db, project.id, scene.script_id)
    if master_scene and master_scene.id != scene.id:
        master = get_active_scene_image_asset(db, master_scene.id)
        add(master.media_asset_id if master else None, "approved_master_identity_reference")

    previous_scene = db.scalars(
        select(Scene)
        .where(
            Scene.script_id == scene.script_id,
            Scene.scene_index < scene.scene_index,
            Scene.approval_status == "approved",
        )
        .order_by(Scene.scene_index.desc())
    ).first()
    if previous_scene:
        previous = get_active_scene_image_asset(db, previous_scene.id)
        add(previous.media_asset_id if previous else None, f"previous_scene_{previous_scene.scene_index:02d}")

    character_id = scene.character_id or project.default_character_id
    character = db.get(Character, character_id) if character_id else None
    if character:
        add(character.primary_image_asset_id, "character_identity_reference")

    background_id = scene.background_id or project.default_background_id
    background = db.get(Background, background_id) if background_id else None
    if background:
        add(background.media_asset_id, "background_reference")

    return refs


def get_master_scene(db: Session, project_id: str, script_id: str) -> Optional[Scene]:
    return db.scalars(
        select(Scene)
        .where(Scene.project_id == project_id, Scene.script_id == script_id)
        .order_by(Scene.scene_index.asc())
    ).first()


def validate_master_gate_for_scene(db: Session, project: Project, scene: Scene) -> Optional[SceneAsset]:
    """
    Spec VII.1: Scene 02+ cannot auto-generate images until Scene 01 Master Image
    exists and is approved (unless project.auto_approve_master is True).
    """
    if scene.is_master_scene or scene.scene_index == 1:
        return None
    master_scene = get_master_scene(db, project.id, scene.script_id)
    if not master_scene:
        raise ContinuityGateError("Scene 01 (Master Scene) was not found for this script.")
    master_asset = get_active_scene_image_asset(db, master_scene.id)
    if not master_asset:
        raise ContinuityGateError(
            "Scene 01 Master Image has not been generated yet. Generate Scene 01 first."
        )
    if not project.auto_approve_master and master_scene.approval_status != "approved":
        raise ContinuityGateError(
            "Scene 01 Master Image must be approved (Approve Master Image) before generating Scene 02+."
        )
    return master_asset


def assemble_continuity_references(
    db: Session,
    *,
    project: Project,
    scene: Scene,
    storage: MediaStorageService,
) -> tuple[list[str], list[str]]:
    """
    Spec VII.1-VII.3: Assemble ordered reference payloads (URL or resized base64 URI)
    and human-readable provenance summary IDs:
    - Scene 01: [Character primary ref, Background ref, optional secondary angle ref]
    - Scene N>1: [Approved Master Image, Character primary ref, Previous Approved Scene Image or Background ref]
    """
    refs: list[str] = []
    summary: list[str] = []
    seen_media_ids: set[str] = set()

    def _append_media(media_id: Optional[str], label: str) -> None:
        if not media_id or media_id in seen_media_ids:
            return
        asset = db.get(MediaAsset, media_id)
        if not asset:
            return
        seen_media_ids.add(media_id)
        refs.append(storage.prepare_reference_for_flow2api(asset))
        summary.append(f"{label}:{asset.id}")

    char_id = scene.character_id or project.default_character_id
    bg_id = scene.background_id or project.default_background_id

    if not scene.is_master_scene and scene.scene_index > 1:
        master_asset = validate_master_gate_for_scene(db, project, scene)
        if master_asset:
            _append_media(master_asset.media_asset_id, "master_image")

    if char_id:
        character = db.get(Character, char_id)
        if character and character.primary_image_asset_id:
            _append_media(character.primary_image_asset_id, "character_primary")
        if scene.is_master_scene or scene.scene_index == 1:
            extra_ref = db.scalars(
                select(CharacterReference)
                .where(
                    CharacterReference.character_id == char_id,
                    CharacterReference.is_primary.is_(False),
                )
                .order_by(CharacterReference.sort_order.asc())
            ).first()
            if extra_ref:
                _append_media(extra_ref.media_asset_id, f"character_{extra_ref.angle_label}")

    if bg_id:
        background = db.get(Background, bg_id)
        if background and background.media_asset_id:
            _append_media(background.media_asset_id, "background")

    if not scene.is_master_scene and scene.scene_index > 1:
        prev_scene = db.scalars(
            select(Scene)
            .where(
                Scene.script_id == scene.script_id,
                Scene.scene_index < scene.scene_index,
                Scene.approval_status == "approved",
            )
            .order_by(Scene.scene_index.desc())
        ).first()
        if prev_scene:
            prev_img = get_active_scene_image_asset(db, prev_scene.id)
            if prev_img and prev_img.media_asset_id not in seen_media_ids:
                # Cap references to 4 for Flow2API reliability
                if len(refs) < 4:
                    _append_media(prev_img.media_asset_id, f"prev_scene_{prev_scene.scene_index:02d}")

    return refs[:4], summary


def evaluate_image_continuity(
    media_asset: MediaAsset, project: Project
) -> tuple[str, str]:
    """Verify aspect ratio and resolution against project settings."""
    if not media_asset.width or not media_asset.height:
        return "warning", "Could not inspect generated image dimensions."
    expected = EXPECTED_RATIOS.get(project.aspect_ratio)
    if not expected:
        return "ok", f"Resolution {media_asset.width}x{media_asset.height} verified."
    actual = media_asset.width / float(media_asset.height)
    rel_diff = abs(actual - expected) / expected
    if rel_diff > 0.08:
        return (
            "warning",
            f"Aspect ratio mismatch: expected {project.aspect_ratio} ({expected:.2f}), "
            f"received {media_asset.width}x{media_asset.height} ({actual:.2f}).",
        )
    return "ok", f"Dimensions {media_asset.width}x{media_asset.height} match {project.aspect_ratio}."


def approve_scene_image(db: Session, scene: Scene) -> list[str]:
    """
    Approve scene image. If Master Scene (Scene 01) is approved after downstream scenes
    already exist, flag downstream scenes as 'needs_review' without force-deleting them.
    """
    scene.approval_status = "approved"
    if scene.status in ("image_ready", "draft", "failed"):
        scene.status = "approved"

    flagged_ids: list[str] = []
    if scene.is_master_scene or scene.scene_index == 1:
        downstream = db.scalars(
            select(Scene).where(
                Scene.script_id == scene.script_id,
                Scene.scene_index > scene.scene_index,
            )
        ).all()
        for ds in downstream:
            existing_img = get_active_scene_image_asset(db, ds.id)
            if existing_img:
                ds.continuity_status = "needs_review"
                ds.continuity_notes = (
                    "Master Image updated/approved; review continuity or regenerate this scene if desired."
                )
                flagged_ids.append(ds.id)
    db.flush()
    return flagged_ids


def activate_scene_asset_version(db: Session, scene_id: str, scene_asset_id: str) -> SceneAsset:
    """Rollback or switch active asset version while keeping version history intact."""
    target = db.get(SceneAsset, scene_asset_id)
    if not target or target.scene_id != scene_id:
        raise ValueError("SceneAsset not found for this scene")

    siblings = db.scalars(
        select(SceneAsset).where(
            SceneAsset.scene_id == scene_id,
            SceneAsset.asset_role == target.asset_role,
        )
    ).all()
    for sib in siblings:
        sib.is_active = sib.id == target.id
    db.flush()
    return target
