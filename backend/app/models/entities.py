import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(50), default="owner", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProviderSetting(Base, TimestampMixin):
    __tablename__ = "provider_settings"
    __table_args__ = (
        Index("ix_provider_settings_owner_key", "owner_id", "provider_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_default: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="configured", nullable=False)


class MediaAsset(Base, TimestampMixin):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL", use_alter=True, name="fk_media_assets_project_id"),
        nullable=True,
        index=True,
    )
    storage_backend: Mapped[str] = mapped_column(String(32), default="local", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    public_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)


class Character(Base, TimestampMixin):
    __tablename__ = "characters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    age_appearance: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    voice_style: Mapped[str] = mapped_column(String(255), default="Natural clear voice", nullable=False)
    role_title: Mapped[str] = mapped_column(String(120), default="Presenter", nullable=False)
    default_screen_time_seconds: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    primary_image_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    default_background_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("backgrounds.id", ondelete="SET NULL", use_alter=True, name="fk_characters_default_bg_id"),
        nullable=True,
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    references: Mapped[list["CharacterReference"]] = relationship(
        back_populates="character", cascade="all, delete-orphan", order_by="CharacterReference.sort_order"
    )
    rules: Mapped[list["CharacterRule"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class CharacterReference(Base, TimestampMixin):
    __tablename__ = "character_references"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    character_id: Mapped[str] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    media_asset_id: Mapped[str] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
    )
    angle_label: Mapped[str] = mapped_column(String(64), default="front", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    character: Mapped["Character"] = relationship(back_populates="references")


class CharacterRule(Base, TimestampMixin):
    __tablename__ = "character_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    character_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), nullable=True, index=True
    )
    preset_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    is_preset: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    preserve_face: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preserve_hair_and_age: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    clothing_rule: Mapped[str] = mapped_column(
        String(255), default="Preserve original face and clothing", nullable=False
    )
    preserve_skin_and_identity: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    natural_movement_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    no_extra_characters: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    preserve_visual_style: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    hand_gesture_level: Mapped[str] = mapped_column(String(32), default="moderate", nullable=False)
    static_camera_on_speech: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    no_auto_bgm: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    custom_directives: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    character: Mapped[Optional["Character"]] = relationship(back_populates="rules")


class Background(Base, TimestampMixin):
    __tablename__ = "backgrounds"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    style: Mapped[str] = mapped_column(String(100), default="photorealistic", nullable=False)
    media_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    cloudinary_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    preservation_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "keep_desk_position": True,
            "keep_camera_angle": True,
            "keep_monitors": True,
            "keep_lighting": True,
            "keep_room_layout": True,
            "seat_character_properly": True,
            "no_extra_objects": True,
            "custom_rules": "",
        },
        nullable=False,
    )
    default_character_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL", use_alter=True, name="fk_backgrounds_default_char_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_category: Mapped[str] = mapped_column(String(64), default="education", nullable=False)
    topic: Mapped[str] = mapped_column(String(1000), nullable=False)
    goal_description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="en-US", nullable=False)
    target_duration_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(16), default="9:16", nullable=False)
    export_quality: Mapped[str] = mapped_column(String(32), default="1080p", nullable=False)
    visual_style: Mapped[str] = mapped_column(String(100), default="photorealistic", nullable=False)
    default_character_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    default_background_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("backgrounds.id", ondelete="SET NULL"), nullable=True
    )
    image_model: Mapped[str] = mapped_column(
        String(150), default="gemini-3.1-flash-image-portrait-2k", nullable=False
    )
    video_model: Mapped[str] = mapped_column(
        String(150), default="omni-1.2-flash-8s-portrait", nullable=False
    )
    voice_mode: Mapped[str] = mapped_column(String(32), default="ai_generated", nullable=False)
    script_mode: Mapped[str] = mapped_column(String(32), default="ai_presenter", nullable=False)
    video_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    global_rules: Mapped[str] = mapped_column(
        Text,
        default="Maintain a consistent character appearance, clothing, lighting, and photorealistic camera continuity across all scenes. Static camera on narration. No background music unless requested.",
        nullable=False,
    )
    character_schedule_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    timeline_config_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "voice_muted": False,
            "voice_volume": 1.0,
            "bgm_muted": False,
            "bgm_volume": 0.15,
            "bgm_asset_id": None,
            "watermark_asset_id": None,
            "intro_asset_id": None,
            "outro_asset_id": None,
            "burn_subtitles": False,
            "default_transition": "none",
            "transition_duration_sec": 0.4,
            "color_grade": True,
        },
        nullable=False,
    )
    auto_approve_master: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)


class Script(Base, TimestampMixin):
    __tablename__ = "scripts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("batch_items.id", ondelete="SET NULL", use_alter=True, name="fk_scripts_batch_item_id"),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="", nullable=False)
    opening_hook: Mapped[str] = mapped_column(Text, default="", nullable=False)
    narration_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    total_estimated_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    llm_provider_used: Mapped[str] = mapped_column(String(100), default="template_fallback", nullable=False)
    raw_payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)


class Scene(Base, TimestampMixin):
    __tablename__ = "scenes"
    __table_args__ = (Index("ix_scenes_project_script_idx", "project_id", "script_id", "scene_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    script_id: Mapped[str] = mapped_column(ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False, index=True)
    scene_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    start_time_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    end_time_sec: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    planned_duration_sec: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    actual_duration_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trim_start_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    trim_end_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    character_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("characters.id", ondelete="SET NULL"), nullable=True
    )
    background_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("backgrounds.id", ondelete="SET NULL"), nullable=True
    )
    dialogue: Mapped[str] = mapped_column(Text, default="", nullable=False)
    caption_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    estimated_speech_sec: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    speech_warning: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    character_action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    camera_motion: Mapped[str] = mapped_column(String(255), default="Static eye-level medium shot", nullable=False)
    transition_type: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_master_scene: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    approval_status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    continuity_status: Mapped[str] = mapped_column(String(32), default="ok", nullable=False)
    continuity_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)


class ScenePrompt(Base, TimestampMixin):
    __tablename__ = "scene_prompts"
    __table_args__ = (Index("ix_scene_prompts_scene_type_active", "scene_id", "prompt_type", "is_active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    prompt_type: Mapped[str] = mapped_column(String(16), nullable=False)  # image | video
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    layers_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    locked_layers_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    compiled_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)


class SceneAsset(Base, TimestampMixin):
    __tablename__ = "scene_assets"
    __table_args__ = (Index("ix_scene_assets_scene_role_active", "scene_id", "asset_role", "is_active"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    media_asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False)
    asset_role: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # master_image | continuity_image | scene_video | external_tts_audio
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    flow_media_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    generation_job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="SET NULL", use_alter=True, name="fk_scene_assets_job_id"),
        nullable=True,
    )
    continuity_metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="ready", nullable=False)


class GenerationJob(Base, TimestampMixin):
    __tablename__ = "generation_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("batches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    batch_item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("batch_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scene_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    render_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("renders.id", ondelete="CASCADE", use_alter=True, name="fk_generation_jobs_render_id"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_model: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status_message: Mapped[str] = mapped_column(String(500), default="Queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(160), unique=True, nullable=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class GenerationAttempt(Base, TimestampMixin):
    __tablename__ = "generation_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("generation_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    model_used: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    prompt_sent: Mapped[str] = mapped_column(Text, default="", nullable=False)
    reference_summary_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    upstream_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="failed", nullable=False)


class Batch(Base, TimestampMixin):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    total_videos: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    completed_videos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_videos: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    request_delay_seconds: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class BatchItem(Base, TimestampMixin):
    __tablename__ = "batch_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    item_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_variant: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    script_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("scripts.id", ondelete="SET NULL"), nullable=True
    )
    render_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("renders.id", ondelete="SET NULL", use_alter=True, name="fk_batch_items_render_id"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class Render(Base, TimestampMixin):
    __tablename__ = "renders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("batch_items.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    output_media_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    subtitle_media_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    thumbnail_media_asset_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    resolution: Mapped[str] = mapped_column(String(32), default="1080x1920", nullable=False)
    render_config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    ffmpeg_log_excerpt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

