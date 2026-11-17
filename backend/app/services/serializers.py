from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import (
    Background,
    Character,
    CharacterRule,
    MediaAsset,
    Project,
    Render,
    Scene,
    SceneAsset,
    ScenePrompt,
    Script,
)
from app.schemas.studio import (
    BackgroundResponse,
    CharacterReferenceResponse,
    CharacterResponse,
    CharacterRuleResponse,
    MediaAssetResponse,
    ProjectResponse,
    RenderResponse,
    SceneAssetInfo,
    ScenePromptInfo,
    SceneResponse,
    ScriptResponse,
)
from app.services.media_storage.manager import MediaStorageService


def serialize_media_asset(asset: MediaAsset, storage: MediaStorageService) -> MediaAssetResponse:
    return MediaAssetResponse(
        id=asset.id,
        storage_backend=asset.storage_backend,
        original_filename=asset.original_filename,
        mime_type=asset.mime_type,
        asset_kind=asset.asset_kind,
        file_size_bytes=asset.file_size_bytes,
        width=asset.width,
        height=asset.height,
        duration_seconds=asset.duration_seconds,
        url=storage.resolve_access_url(asset),
        public_url=asset.public_url,
        project_id=asset.project_id,
        created_at=asset.created_at,
    )


def serialize_character_rule(rule: CharacterRule) -> CharacterRuleResponse:
    return CharacterRuleResponse(
        id=rule.id,
        character_id=rule.character_id,
        preset_name=rule.preset_name,
        is_preset=rule.is_preset,
        preserve_face=rule.preserve_face,
        preserve_hair_and_age=rule.preserve_hair_and_age,
        clothing_rule=rule.clothing_rule,
        preserve_skin_and_identity=rule.preserve_skin_and_identity,
        natural_movement_only=rule.natural_movement_only,
        no_extra_characters=rule.no_extra_characters,
        preserve_visual_style=rule.preserve_visual_style,
        hand_gesture_level=rule.hand_gesture_level,
        static_camera_on_speech=rule.static_camera_on_speech,
        no_auto_bgm=rule.no_auto_bgm,
        custom_directives=rule.custom_directives,
        created_at=rule.created_at,
    )


def serialize_character(
    db: Session, char: Character, storage: MediaStorageService
) -> CharacterResponse:
    primary_url: Optional[str] = None
    if char.primary_image_asset_id:
        if ma := db.get(MediaAsset, char.primary_image_asset_id):
            primary_url = storage.resolve_access_url(ma)

    refs: list[CharacterReferenceResponse] = []
    for ref in char.references:
        ref_url = ""
        if ma := db.get(MediaAsset, ref.media_asset_id):
            ref_url = storage.resolve_access_url(ma)
        refs.append(
            CharacterReferenceResponse(
                id=ref.id,
                media_asset_id=ref.media_asset_id,
                angle_label=ref.angle_label,
                is_primary=ref.is_primary,
                sort_order=ref.sort_order,
                url=ref_url,
            )
        )

    rule_resp = serialize_character_rule(char.rules[0]) if char.rules else None
    usage_count = (
        db.scalar(
            select(func.count(Project.id)).where(
                Project.owner_id == char.owner_id,
                Project.default_character_id == char.id,
            )
        )
        or 0
    )

    return CharacterResponse(
        id=char.id,
        name=char.name,
        description=char.description,
        age_appearance=char.age_appearance,
        voice_style=char.voice_style,
        role_title=char.role_title,
        default_screen_time_seconds=char.default_screen_time_seconds,
        primary_image_asset_id=char.primary_image_asset_id,
        primary_image_url=primary_url,
        default_background_id=char.default_background_id,
        is_default=char.is_default,
        references=refs,
        rule=rule_resp,
        project_usage_count=int(usage_count),
        created_at=char.created_at,
    )


def serialize_background(
    db: Session, bg: Background, storage: MediaStorageService
) -> BackgroundResponse:
    img_url = bg.cloudinary_url
    if bg.media_asset_id:
        if ma := db.get(MediaAsset, bg.media_asset_id):
            img_url = storage.resolve_access_url(ma)
    usage_count = (
        db.scalar(
            select(func.count(Project.id)).where(
                Project.owner_id == bg.owner_id,
                Project.default_background_id == bg.id,
            )
        )
        or 0
    )
    return BackgroundResponse(
        id=bg.id,
        name=bg.name,
        description=bg.description,
        style=bg.style,
        media_asset_id=bg.media_asset_id,
        image_url=img_url,
        cloudinary_url=bg.cloudinary_url,
        preservation_rules_json=bg.preservation_rules_json or {},
        default_character_id=bg.default_character_id,
        project_usage_count=int(usage_count),
        created_at=bg.created_at,
    )


def serialize_scene(db: Session, scene: Scene, storage: MediaStorageService) -> SceneResponse:
    char_name = None
    if scene.character_id:
        if c := db.get(Character, scene.character_id):
            char_name = c.name
    bg_name = None
    if scene.background_id:
        if b := db.get(Background, scene.background_id):
            bg_name = b.name

    prompts = db.scalars(
        select(ScenePrompt)
        .where(ScenePrompt.scene_id == scene.id, ScenePrompt.is_active.is_(True))
        .order_by(ScenePrompt.prompt_type.asc())
    ).all()
    prompt_infos = [
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

    scene_assets = db.scalars(
        select(SceneAsset)
        .where(SceneAsset.scene_id == scene.id)
        .order_by(SceneAsset.asset_role.asc(), SceneAsset.version.desc())
    ).all()
    asset_infos: list[SceneAssetInfo] = []
    active_image_url: Optional[str] = None
    active_video_url: Optional[str] = None
    for sa in scene_assets:
        ma = db.get(MediaAsset, sa.media_asset_id)
        if not ma:
            continue
        url = storage.resolve_access_url(ma)
        if sa.is_active and sa.asset_role in ("master_image", "continuity_image"):
            active_image_url = url
        elif sa.is_active and sa.asset_role == "scene_video":
            active_video_url = url
        asset_infos.append(
            SceneAssetInfo(
                id=sa.id,
                media_asset_id=sa.media_asset_id,
                asset_role=sa.asset_role,
                version=sa.version,
                is_active=sa.is_active,
                flow_media_id=sa.flow_media_id,
                url=url,
                width=ma.width,
                height=ma.height,
                duration_seconds=ma.duration_seconds,
                continuity_metadata_json=sa.continuity_metadata_json or {},
                created_at=sa.created_at,
            )
        )

    return SceneResponse(
        id=scene.id,
        project_id=scene.project_id,
        script_id=scene.script_id,
        scene_index=scene.scene_index,
        title=scene.title,
        start_time_sec=scene.start_time_sec,
        end_time_sec=scene.end_time_sec,
        planned_duration_sec=scene.planned_duration_sec,
        actual_duration_sec=scene.actual_duration_sec,
        trim_start_sec=scene.trim_start_sec,
        trim_end_sec=scene.trim_end_sec,
        character_id=scene.character_id,
        character_name=char_name,
        background_id=scene.background_id,
        background_name=bg_name,
        dialogue=scene.dialogue,
        caption_text=scene.caption_text,
        estimated_speech_sec=scene.estimated_speech_sec,
        speech_warning=scene.speech_warning,
        character_action=scene.character_action,
        camera_motion=scene.camera_motion,
        transition_type=scene.transition_type,
        is_locked=scene.is_locked,
        is_master_scene=scene.is_master_scene,
        approval_status=scene.approval_status,
        continuity_status=scene.continuity_status,
        continuity_notes=scene.continuity_notes,
        status=scene.status,
        image_url=active_image_url,
        video_url=active_video_url,
        prompts=prompt_infos,
        assets=asset_infos,
    )


def serialize_script(db: Session, script: Script, storage: MediaStorageService) -> ScriptResponse:
    scenes = db.scalars(
        select(Scene)
        .where(Scene.script_id == script.id)
        .order_by(Scene.scene_index.asc())
    ).all()
    return ScriptResponse(
        id=script.id,
        project_id=script.project_id,
        version=script.version,
        is_active=script.is_active,
        title=script.title,
        objective=script.objective,
        opening_hook=script.opening_hook,
        narration_summary=script.narration_summary,
        total_estimated_seconds=script.total_estimated_seconds,
        llm_provider_used=script.llm_provider_used,
        status=script.status,
        scenes=[serialize_scene(db, s, storage) for s in scenes],
        created_at=script.created_at,
    )


def serialize_project(db: Session, project: Project) -> ProjectResponse:
    active_script = db.scalars(
        select(Script)
        .where(Script.project_id == project.id, Script.is_active.is_(True))
        .order_by(Script.version.desc())
    ).first()
    scenes: list[Scene] = []
    if active_script:
        scenes = db.scalars(select(Scene).where(Scene.script_id == active_script.id)).all()

    ready_images = sum(
        1 for s in scenes if s.status in ("image_ready", "approved", "video_queued", "video_generating", "video_ready")
    )
    ready_videos = sum(1 for s in scenes if s.status == "video_ready")
    completed_renders = (
        db.scalar(
            select(func.count(Render.id)).where(
                Render.project_id == project.id, Render.status == "completed"
            )
        )
        or 0
    )

    return ProjectResponse(
        id=project.id,
        name=project.name,
        topic_category=project.topic_category,
        topic=project.topic,
        goal_description=project.goal_description,
        language=project.language,
        target_duration_seconds=project.target_duration_seconds,
        aspect_ratio=project.aspect_ratio,
        export_quality=project.export_quality,
        visual_style=project.visual_style,
        default_character_id=project.default_character_id,
        default_background_id=project.default_background_id,
        image_model=project.image_model,
        video_model=project.video_model,
        voice_mode=project.voice_mode,
        script_mode=project.script_mode,
        voice_description=str((project.timeline_config_json or {}).get("voice_description") or ""),
        video_count=project.video_count,
        global_rules=project.global_rules,
        character_schedule_json=project.character_schedule_json or [],
        timeline_config_json=project.timeline_config_json or {},
        auto_approve_master=project.auto_approve_master,
        status=project.status,
        scene_count=len(scenes),
        ready_images_count=ready_images,
        ready_videos_count=ready_videos,
        completed_renders_count=int(completed_renders),
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def serialize_render(db: Session, render: Render, storage: MediaStorageService) -> RenderResponse:
    proj = db.get(Project, render.project_id)
    mp4_url = None
    srt_url = None
    thumb_url = None
    if render.output_media_asset_id:
        if ma := db.get(MediaAsset, render.output_media_asset_id):
            mp4_url = storage.resolve_access_url(ma)
    if render.subtitle_media_asset_id:
        if sa := db.get(MediaAsset, render.subtitle_media_asset_id):
            srt_url = storage.resolve_access_url(sa)
    if render.thumbnail_media_asset_id:
        if ta := db.get(MediaAsset, render.thumbnail_media_asset_id):
            thumb_url = storage.resolve_access_url(ta)

    return RenderResponse(
        id=render.id,
        project_id=render.project_id,
        project_name=proj.name if proj else None,
        version=render.version,
        status=render.status,
        duration_seconds=render.duration_seconds,
        file_size_bytes=render.file_size_bytes,
        resolution=render.resolution,
        mp4_url=mp4_url,
        srt_url=srt_url,
        thumbnail_url=thumb_url,
        error_message=render.error_message,
        ffmpeg_log_excerpt=render.ffmpeg_log_excerpt,
        created_at=render.created_at,
    )
