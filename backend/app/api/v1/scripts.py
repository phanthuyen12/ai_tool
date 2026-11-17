from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import (
    Background,
    Character,
    CharacterRule,
    Project,
    Scene,
    ScenePrompt,
    Script,
    User,
)
from app.schemas.studio import (
    SceneCreateRequest,
    SceneResponse,
    ScriptGenerateRequest,
    ScriptResponse,
)
from app.services.ai_script.llm_generator import (
    compute_scene_speech_metrics,
    generate_script_draft,
)
from app.services.ai_script.scene_planner import (
    parse_model_clip_duration,
    plan_scene_windows,
)
from app.services.media_storage.manager import MediaStorageService
from app.services.prompt_builder.builder import (
    build_image_prompt_layers,
    build_video_prompt_layers,
)
from app.services.provider_config import get_effective_runtime_config
from app.services.serializers import serialize_scene, serialize_script

router = APIRouter(prefix="/scripts", tags=["scripts"])


def create_scene_prompts_for_scene(
    db: Session, *, project: Project, scene: Scene
) -> None:
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
        select(Scene)
        .where(Scene.script_id == scene.script_id)
        .order_by(Scene.scene_index.asc())
    ).first()
    master_char_differs = bool(
        master_scene and scene.scene_index > 1 and master_scene.character_id != scene.character_id
    )

    img_layers, img_compiled = build_image_prompt_layers(
        scene=scene,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
        is_master_scene=scene.is_master_scene or scene.scene_index == 1,
        has_prev_scene=scene.scene_index > 2,
        master_character_differs=master_char_differs,
    )
    vid_layers, vid_compiled = build_video_prompt_layers(
        scene=scene,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
    )

    db.add(
        ScenePrompt(
            scene_id=scene.id,
            prompt_type="image",
            version=1,
            is_active=True,
            layers_json=img_layers,
            locked_layers_json=[],
            compiled_prompt=img_compiled,
        )
    )
    db.add(
        ScenePrompt(
            scene_id=scene.id,
            prompt_type="video",
            version=1,
            is_active=True,
            layers_json=vid_layers,
            locked_layers_json=[],
            compiled_prompt=vid_compiled,
        )
    )


@router.get("", response_model=list[ScriptResponse])
def list_scripts(
    project_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    scripts = db.scalars(
        select(Script)
        .where(Script.project_id == project.id)
        .order_by(Script.version.desc())
    ).all()
    return [serialize_script(db, s, storage) for s in scripts]


@router.post("/generate", response_model=ScriptResponse, status_code=201)
def generate_script(
    payload: ScriptGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    project.status = "script_generating"
    db.flush()

    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    target_duration = payload.target_duration_seconds or project.target_duration_seconds
    clip_dur = parse_model_clip_duration(project.video_model, default=8)
    windows = plan_scene_windows(
        target_duration_sec=target_duration,
        clip_duration_sec=clip_dur,
        character_schedule=project.character_schedule_json,
        default_character_id=project.default_character_id,
        default_background_id=project.default_background_id,
    )

    chars = db.scalars(select(Character).where(Character.owner_id == current_user.id)).all()
    char_names: dict[Optional[str], str] = {c.id: c.name for c in chars}

    draft = generate_script_draft(
        runtime_cfg=runtime,
        topic=payload.topic_override or project.topic,
        topic_category=project.topic_category,
        goal_description=project.goal_description,
        language=project.language,
        visual_style=project.visual_style,
        windows=windows,
        character_names=char_names,
        voice_mode=project.voice_mode,
        script_mode=project.script_mode,
    )

    # Versioning & locked-scene preservation (Spec V.4)
    prev_scripts = db.scalars(
        select(Script).where(Script.project_id == project.id).order_by(Script.version.desc())
    ).all()
    prev_active = next((s for s in prev_scripts if s.is_active), None)
    locked_by_index: dict[int, Scene] = {}
    if prev_active and not payload.force_overwrite_locked:
        prev_scenes = db.scalars(
            select(Scene).where(Scene.script_id == prev_active.id)
        ).all()
        for ps in prev_scenes:
            if ps.is_locked or ps.approval_status == "approved":
                locked_by_index[ps.scene_index] = ps

    next_version = (prev_scripts[0].version if prev_scripts else 0) + 1
    for ps in prev_scripts:
        ps.is_active = False

    script = Script(
        project_id=project.id,
        version=next_version,
        is_active=True,
        # Script generators may return a full narration as the title. The DB
        # column is intentionally a short label, so keep it within its limit.
        title=draft.title.strip()[:300],
        objective=draft.objective,
        opening_hook=draft.opening_hook,
        narration_summary=draft.narration_summary,
        total_estimated_seconds=float(sum(w.duration_sec for w in windows)),
        llm_provider_used=draft.llm_provider_used,
        raw_payload_json=draft.raw_payload,
        status="ready",
    )
    db.add(script)
    db.flush()

    for win, sc_draft in zip(windows, draft.scenes):
        locked_prev = locked_by_index.get(win.scene_index)
        if locked_prev:
            est, warn = compute_scene_speech_metrics(
                locked_prev.dialogue, locked_prev.planned_duration_sec, project.language
            )
            scene = Scene(
                project_id=project.id,
                script_id=script.id,
                scene_index=win.scene_index,
                title=locked_prev.title,
                start_time_sec=win.start_time_sec,
                end_time_sec=win.end_time_sec,
                planned_duration_sec=locked_prev.planned_duration_sec,
                character_id=locked_prev.character_id,
                background_id=locked_prev.background_id,
                dialogue=locked_prev.dialogue,
                caption_text=locked_prev.caption_text,
                estimated_speech_sec=est,
                speech_warning=warn,
                character_action=locked_prev.character_action,
                camera_motion=locked_prev.camera_motion,
                transition_type=locked_prev.transition_type,
                is_locked=True,
                is_master_scene=win.is_master_scene,
                approval_status="pending",
                status="draft",
            )
        else:
            est, warn = compute_scene_speech_metrics(
                sc_draft.dialogue, win.duration_sec, project.language
            )
            scene = Scene(
                project_id=project.id,
                script_id=script.id,
                scene_index=win.scene_index,
                title=sc_draft.title,
                start_time_sec=win.start_time_sec,
                end_time_sec=win.end_time_sec,
                planned_duration_sec=win.duration_sec,
                character_id=win.character_id,
                background_id=win.background_id,
                dialogue=sc_draft.dialogue,
                caption_text=sc_draft.caption_text,
                estimated_speech_sec=est,
                speech_warning=warn,
                character_action=sc_draft.character_action,
                camera_motion=sc_draft.camera_motion,
                transition_type=sc_draft.transition_type,
                is_locked=False,
                is_master_scene=win.is_master_scene,
                approval_status="pending",
                status="draft",
            )
        db.add(scene)
        db.flush()
        create_scene_prompts_for_scene(db, project=project, scene=scene)

    project.status = "script_ready"
    db.commit()
    db.refresh(script)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_script(db, script, storage)


@router.get("/{script_id}", response_model=ScriptResponse)
def get_script(
    script_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    project = db.get(Project, script.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Script not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_script(db, script, storage)


@router.post("/{script_id}/activate", response_model=ScriptResponse)
def activate_script_version(
    script_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    project = db.get(Project, script.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Script not found")

    siblings = db.scalars(select(Script).where(Script.project_id == project.id)).all()
    for s in siblings:
        s.is_active = s.id == script.id
    db.commit()
    db.refresh(script)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_script(db, script, storage)


@router.post("/{script_id}/scenes", response_model=SceneResponse, status_code=201)
def add_scene_to_script(
    script_id: str,
    payload: SceneCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    script = db.get(Script, script_id)
    if not script:
        raise HTTPException(status_code=404, detail="Script not found")
    project = db.get(Project, script.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Script not found")

    existing = db.scalars(
        select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_index.asc())
    ).all()
    next_idx = (existing[-1].scene_index if existing else 0) + 1
    start_s = existing[-1].end_time_sec if existing else 0.0
    end_s = round(start_s + payload.planned_duration_sec, 2)
    est, warn = compute_scene_speech_metrics(
        payload.dialogue, payload.planned_duration_sec, project.language
    )

    scene = Scene(
        project_id=project.id,
        script_id=script.id,
        scene_index=next_idx,
        title=payload.title,
        start_time_sec=start_s,
        end_time_sec=end_s,
        planned_duration_sec=payload.planned_duration_sec,
        character_id=payload.character_id or project.default_character_id,
        background_id=payload.background_id or project.default_background_id,
        dialogue=payload.dialogue,
        caption_text=payload.caption_text or payload.dialogue,
        estimated_speech_sec=est,
        speech_warning=warn,
        character_action=payload.character_action,
        camera_motion=payload.camera_motion,
        transition_type=payload.transition_type,
        is_master_scene=(next_idx == 1),
    )
    db.add(scene)
    db.flush()
    create_scene_prompts_for_scene(db, project=project, scene=scene)
    db.commit()
    db.refresh(scene)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_scene(db, scene, storage)
