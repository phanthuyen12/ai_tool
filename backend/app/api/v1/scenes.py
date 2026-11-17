import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import (
    Background,
    Character,
    CharacterRule,
    GenerationJob,
    MediaAsset,
    Project,
    Scene,
    SceneAsset,
    ScenePrompt,
    Script,
    User,
)
from app.schemas.studio import (
    GenerateSceneMediaRequest,
    GenerationJobResponse,
    ScenePromptInfo,
    ScenePromptUpdateRequest,
    SceneResponse,
    SceneUpdateRequest,
)
from app.services.ai_script.llm_generator import compute_scene_speech_metrics
from app.services.ai_script.scene_planner import trim_dialogue_to_fit
from app.services.generation.continuity import (
    ContinuityGateError,
    activate_scene_asset_version,
    approve_scene_image,
    get_active_scene_image_asset,
    validate_master_gate_for_scene,
)
from app.services.media_storage.manager import MediaStorageService
from app.services.prompt_builder.builder import (
    build_image_prompt_layers,
    build_video_prompt_layers,
    compile_layers,
)
from app.services.serializers import serialize_scene
from app.workers.tasks import run_scene_image_job, run_scene_video_job

router = APIRouter(prefix="/scenes", tags=["scenes"])


def _get_owned_scene(db: Session, scene_id: str, owner_id: str) -> tuple[Scene, Project]:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise HTTPException(status_code=404, detail="Scene not found")
    project = db.get(Project, scene.project_id)
    if not project or project.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Scene not found")
    return scene, project


def _refresh_scene_prompts(db: Session, project: Project, scene: Scene, force_types: Optional[list[str]] = None) -> None:
    char = (
        db.get(Character, scene.character_id or project.default_character_id)
        if (scene.character_id or project.default_character_id)
        else None
    )
    rule = (
        db.scalars(select(CharacterRule).where(CharacterRule.character_id == char.id)).first()
        if char
        else None
    )
    bg = (
        db.get(Background, scene.background_id or project.default_background_id)
        if (scene.background_id or project.default_background_id)
        else None
    )
    master_scene = db.scalars(
        select(Scene).where(Scene.script_id == scene.script_id).order_by(Scene.scene_index.asc())
    ).first()
    master_differs = bool(
        master_scene and scene.scene_index > 1 and master_scene.character_id != scene.character_id
    )

    for ptype in force_types or ["image", "video"]:
        active = db.scalars(
            select(ScenePrompt).where(
                ScenePrompt.scene_id == scene.id,
                ScenePrompt.prompt_type == ptype,
                ScenePrompt.is_active.is_(True),
            )
        ).first()
        if active and active.is_manual_override and not force_types:
            continue
        existing_layers = active.layers_json if active else None
        locked = active.locked_layers_json if active else []

        if ptype == "image":
            layers, compiled = build_image_prompt_layers(
                scene=scene,
                project=project,
                character=char,
                char_rule=rule,
                background=bg,
                is_master_scene=scene.is_master_scene or scene.scene_index == 1,
                has_prev_scene=scene.scene_index > 2,
                master_character_differs=master_differs,
                existing_layers=existing_layers,
                locked_layers=locked,
            )
        else:
            layers, compiled = build_video_prompt_layers(
                scene=scene,
                project=project,
                character=char,
                char_rule=rule,
                background=bg,
                existing_layers=existing_layers,
                locked_layers=locked,
            )

        if active:
            active.layers_json = layers
            active.compiled_prompt = compiled
            active.is_manual_override = False
        else:
            db.add(
                ScenePrompt(
                    scene_id=scene.id,
                    prompt_type=ptype,
                    version=1,
                    is_active=True,
                    layers_json=layers,
                    locked_layers_json=locked,
                    compiled_prompt=compiled,
                )
            )


def _reindex_script_scenes(db: Session, script_id: str) -> None:
    scenes = db.scalars(
        select(Scene).where(Scene.script_id == script_id).order_by(Scene.scene_index.asc())
    ).all()
    cursor = 0.0
    for idx, sc in enumerate(scenes, start=1):
        sc.scene_index = idx
        sc.is_master_scene = idx == 1
        dur = sc.actual_duration_sec or sc.planned_duration_sec
        sc.start_time_sec = round(cursor, 2)
        sc.end_time_sec = round(cursor + dur, 2)
        cursor += dur


@router.get("", response_model=list[SceneResponse])
def list_scenes(
    project_id: str = Query(...),
    script_id: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    target_script_id = script_id
    if not target_script_id:
        active_script = db.scalars(
            select(Script)
            .where(Script.project_id == project.id, Script.is_active.is_(True))
            .order_by(Script.version.desc())
        ).first()
        if not active_script:
            return []
        target_script_id = active_script.id

    rows = db.scalars(
        select(Scene).where(Scene.script_id == target_script_id).order_by(Scene.scene_index.asc())
    ).all()
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return [serialize_scene(db, s, storage) for s in rows]


@router.get("/{scene_id}", response_model=SceneResponse)
def get_scene(
    scene_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)


@router.patch("/{scene_id}", response_model=SceneResponse)
def update_scene(
    scene_id: str,
    payload: SceneUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    if scene.is_locked and payload.is_locked is not False and (
        payload.dialogue is not None or payload.character_action is not None
    ):
        raise HTTPException(
            status_code=409,
            detail="Scene is locked. Unlock the scene before modifying its dialogue or action.",
        )

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(scene, k, v)

    dur = scene.actual_duration_sec or scene.planned_duration_sec
    est, warn = compute_scene_speech_metrics(scene.dialogue, dur, project.language)
    scene.estimated_speech_sec = est
    scene.speech_warning = warn
    if payload.planned_duration_sec is not None:
        _reindex_script_scenes(db, scene.script_id)

    _refresh_scene_prompts(db, project, scene)
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)


@router.post("/{scene_id}/auto-fit-dialogue", response_model=SceneResponse)
def auto_fit_scene_dialogue(
    scene_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    if scene.is_locked:
        raise HTTPException(status_code=409, detail="Cannot rewrite dialogue on a locked scene")
    dur = scene.actual_duration_sec or scene.planned_duration_sec
    fitted = trim_dialogue_to_fit(scene.dialogue, dur, project.language)
    scene.dialogue = fitted
    scene.caption_text = fitted
    est, warn = compute_scene_speech_metrics(fitted, dur, project.language)
    scene.estimated_speech_sec = est
    scene.speech_warning = warn
    _refresh_scene_prompts(db, project, scene)
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)


@router.delete("/{scene_id}", status_code=204)
def delete_scene(
    scene_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    script_id = scene.script_id
    db.delete(scene)
    db.flush()
    _reindex_script_scenes(db, script_id)
    db.commit()
    return None


@router.get("/{scene_id}/prompts", response_model=list[ScenePromptInfo])
def get_scene_prompts(
    scene_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    prompts = db.scalars(
        select(ScenePrompt)
        .where(ScenePrompt.scene_id == scene.id, ScenePrompt.is_active.is_(True))
        .order_by(ScenePrompt.prompt_type.asc())
    ).all()
    return [
        ScenePromptInfo(
            id=p.id,
            prompt_type=p.prompt_type,
            version=p.version,
            is_active=p.is_active,
            layers_json=p.layers_json or {},
            locked_layers_json=p.locked_layers_json or [],
            compiled_prompt=p.compiled_prompt,
            is_manual_override=p.is_manual_override,
        )
        for p in prompts
    ]


@router.put("/{scene_id}/prompts", response_model=ScenePromptInfo)
def update_scene_prompt(
    scene_id: str,
    payload: ScenePromptUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    existing = db.scalars(
        select(ScenePrompt)
        .where(
            ScenePrompt.scene_id == scene.id,
            ScenePrompt.prompt_type == payload.prompt_type,
            ScenePrompt.is_active.is_(True),
        )
        .order_by(ScenePrompt.version.desc())
    ).first()

    if payload.rebuild_from_rules:
        if existing and payload.locked_layers_json is not None:
            existing.locked_layers_json = payload.locked_layers_json
        _refresh_scene_prompts(db, project, scene, force_types=[payload.prompt_type])
        db.commit()
        refreshed = db.scalars(
            select(ScenePrompt).where(
                ScenePrompt.scene_id == scene.id,
                ScenePrompt.prompt_type == payload.prompt_type,
                ScenePrompt.is_active.is_(True),
            )
        ).first()
        return ScenePromptInfo(
            id=refreshed.id,
            prompt_type=refreshed.prompt_type,
            version=refreshed.version,
            is_active=refreshed.is_active,
            layers_json=refreshed.layers_json or {},
            locked_layers_json=refreshed.locked_layers_json or [],
            compiled_prompt=refreshed.compiled_prompt,
            is_manual_override=refreshed.is_manual_override,
        )

    # Create a new active prompt version preserving history
    next_version = (existing.version if existing else 0) + 1
    layers = dict(existing.layers_json) if existing else {}
    if payload.layers_json is not None:
        layers.update(payload.layers_json)
    locked = (
        payload.locked_layers_json
        if payload.locked_layers_json is not None
        else (existing.locked_layers_json if existing else [])
    )
    compiled = (
        payload.compiled_prompt
        if (payload.is_manual_override and payload.compiled_prompt is not None)
        else compile_layers(layers)
    )
    is_manual = bool(payload.is_manual_override)

    if existing:
        existing.is_active = False
    new_prompt = ScenePrompt(
        scene_id=scene.id,
        prompt_type=payload.prompt_type,
        version=next_version,
        is_active=True,
        layers_json=layers,
        locked_layers_json=locked,
        compiled_prompt=compiled,
        is_manual_override=is_manual,
    )
    db.add(new_prompt)
    db.commit()
    db.refresh(new_prompt)
    return ScenePromptInfo(
        id=new_prompt.id,
        prompt_type=new_prompt.prompt_type,
        version=new_prompt.version,
        is_active=new_prompt.is_active,
        layers_json=new_prompt.layers_json or {},
        locked_layers_json=new_prompt.locked_layers_json or [],
        compiled_prompt=new_prompt.compiled_prompt,
        is_manual_override=new_prompt.is_manual_override,
    )


def _job_to_resp(job: GenerationJob) -> GenerationJobResponse:
    return GenerationJobResponse(
        id=job.id,
        project_id=job.project_id,
        batch_id=job.batch_id,
        scene_id=job.scene_id,
        render_id=job.render_id,
        job_type=job.job_type,
        provider_model=job.provider_model,
        status=job.status,
        progress_percent=job.progress_percent,
        status_message=job.status_message,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        error_code=job.error_code,
        error_message=job.error_message,
        result_json=job.result_json or {},
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        attempts=[],
    )


@router.post("/{scene_id}/generate-image", response_model=GenerationJobResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_scene_image(
    scene_id: str,
    payload: GenerateSceneMediaRequest = GenerateSceneMediaRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    try:
        validate_master_gate_for_scene(db, project, scene)
    except ContinuityGateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    model = (payload.model_override or project.image_model).strip()
    job = GenerationJob(
        owner_id=current_user.id,
        project_id=project.id,
        scene_id=scene.id,
        job_type="scene_image",
        provider_model=model,
        status="queued",
        progress_percent=0,
        status_message=f"Queued image generation ({model})",
        max_attempts=3,
        idempotency_key=f"img:{scene.id}:{uuid.uuid4().hex[:10]}",
    )
    scene.status = "image_queued"
    db.add(job)
    db.commit()
    db.refresh(job)

    run_scene_image_job.send(job.id)
    return _job_to_resp(job)


@router.post("/{scene_id}/generate-video", response_model=GenerationJobResponse, status_code=status.HTTP_202_ACCEPTED)
def generate_scene_video(
    scene_id: str,
    payload: GenerateSceneMediaRequest = GenerateSceneMediaRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    # Avoid submitting duplicate provider jobs from repeated clicks or stale UI.
    existing_job = db.scalars(
        select(GenerationJob)
        .where(
            GenerationJob.scene_id == scene.id,
            GenerationJob.job_type == "scene_video",
            GenerationJob.status.in_(["queued", "running"]),
        )
        .order_by(GenerationJob.created_at.desc())
    ).first()
    if existing_job:
        return _job_to_resp(existing_job)

    active_img = get_active_scene_image_asset(db, scene.id)
    if not active_img:
        raise HTTPException(
            status_code=409,
            detail="Generate a reference image for this scene before creating video.",
        )
    if scene.approval_status != "approved" and not project.auto_approve_master:
        raise HTTPException(
            status_code=409,
            detail="Approve this scene's reference image before generating video.",
        )

    model = (payload.model_override or project.video_model).strip()
    job = GenerationJob(
        owner_id=current_user.id,
        project_id=project.id,
        scene_id=scene.id,
        job_type="scene_video",
        provider_model=model,
        status="queued",
        progress_percent=0,
        status_message=f"Queued video generation ({model})",
        max_attempts=3,
        idempotency_key=f"vid:{scene.id}:{uuid.uuid4().hex[:10]}",
    )
    scene.status = "video_queued"
    db.add(job)
    db.commit()
    db.refresh(job)

    run_scene_video_job.send(job.id)
    return _job_to_resp(job)


@router.post("/{scene_id}/approve")
def approve_scene(
    scene_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, project = _get_owned_scene(db, scene_id, current_user.id)
    active_img = get_active_scene_image_asset(db, scene.id)
    if not active_img:
        raise HTTPException(status_code=409, detail="Cannot approve scene before an image is generated")
    flagged_ids = approve_scene_image(db, scene)
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return {
        "scene": serialize_scene(db, scene, storage),
        "flagged_downstream_scene_ids": flagged_ids,
    }


@router.post("/{scene_id}/assets/{scene_asset_id}/activate", response_model=SceneResponse)
def rollback_scene_asset(
    scene_id: str,
    scene_asset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    try:
        activate_scene_asset_version(db, scene.id, scene_asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)


@router.post("/{scene_id}/attach-tts", response_model=SceneResponse)
def attach_external_tts(
    scene_id: str,
    media_asset_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    prev = db.scalars(
        select(SceneAsset).where(
            SceneAsset.scene_id == scene.id, SceneAsset.asset_role == "external_tts_audio"
        )
    ).all()
    next_ver = max((p.version for p in prev), default=0) + 1
    for p in prev:
        p.is_active = False
    db.add(
        SceneAsset(
            scene_id=scene.id,
            media_asset_id=media_asset_id,
            asset_role="external_tts_audio",
            version=next_ver,
            is_active=True,
        )
    )
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)


@router.post("/{scene_id}/attach-asset", response_model=SceneResponse)
def attach_scene_asset(
    scene_id: str,
    media_asset_id: str = Query(...),
    asset_role: str = Query(..., pattern="^(master_image|continuity_image|scene_video|external_tts_audio)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scene, _ = _get_owned_scene(db, scene_id, current_user.id)
    media = db.get(MediaAsset, media_asset_id)
    if not media or media.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="MediaAsset not found")

    prev = db.scalars(
        select(SceneAsset).where(
            SceneAsset.scene_id == scene.id, SceneAsset.asset_role == asset_role
        )
    ).all()
    next_ver = max((p.version for p in prev), default=0) + 1
    for p in prev:
        p.is_active = False

    db.add(
        SceneAsset(
            scene_id=scene.id,
            media_asset_id=media.id,
            asset_role=asset_role,
            version=next_ver,
            is_active=True,
        )
    )
    if asset_role in ("master_image", "continuity_image") and scene.status in ("draft", "failed"):
        scene.status = "image_ready"
    elif asset_role == "scene_video":
        scene.status = "video_ready"
        if media.duration_seconds:
            scene.actual_duration_sec = media.duration_seconds
            _reindex_script_scenes(db, scene.script_id)

    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)
