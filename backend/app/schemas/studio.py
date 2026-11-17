from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# --- Auth ---
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class RegisterRequest(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=6)


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# --- Media ---
class ImportUrlRequest(BaseModel):
    url: str
    asset_kind: str = Field(default="image", pattern="^(image|video|audio)$")
    project_id: Optional[str] = None
    filename: Optional[str] = None


class MediaAssetResponse(BaseModel):
    id: str
    storage_backend: str
    original_filename: str
    mime_type: str
    asset_kind: str
    file_size_bytes: int
    width: Optional[int]
    height: Optional[int]
    duration_seconds: Optional[float]
    url: str
    public_url: Optional[str]
    project_id: Optional[str]
    created_at: datetime


# --- Characters & Rules ---
class CharacterRulePayload(BaseModel):
    preset_name: Optional[str] = None
    is_preset: bool = False
    preserve_face: bool = True
    preserve_hair_and_age: bool = True
    clothing_rule: str = "Preserve original face and clothing"
    preserve_skin_and_identity: bool = True
    natural_movement_only: bool = True
    no_extra_characters: bool = True
    preserve_visual_style: bool = True
    hand_gesture_level: str = "moderate"
    static_camera_on_speech: bool = True
    no_auto_bgm: bool = True
    custom_directives: str = ""


class CharacterRuleResponse(CharacterRulePayload):
    id: str
    character_id: Optional[str]
    created_at: datetime


class CharacterReferenceCreate(BaseModel):
    media_asset_id: str
    angle_label: str = "front"
    is_primary: bool = False


class CharacterReferenceResponse(BaseModel):
    id: str
    media_asset_id: str
    angle_label: str
    is_primary: bool
    sort_order: int
    url: str


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    age_appearance: Optional[str] = None
    voice_style: str = "Mature clear voice"
    role_title: str = "Presenter"
    default_screen_time_seconds: int = Field(default=30, ge=4, le=600)
    primary_image_asset_id: Optional[str] = None
    default_background_id: Optional[str] = None
    is_default: bool = False
    reference_asset_ids: list[str] = Field(default_factory=list)
    rule: Optional[CharacterRulePayload] = None


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    age_appearance: Optional[str] = None
    voice_style: Optional[str] = None
    role_title: Optional[str] = None
    default_screen_time_seconds: Optional[int] = Field(default=None, ge=4, le=600)
    primary_image_asset_id: Optional[str] = None
    default_background_id: Optional[str] = None
    is_default: Optional[bool] = None
    rule: Optional[CharacterRulePayload] = None


class CharacterResponse(BaseModel):
    id: str
    name: str
    description: str
    age_appearance: Optional[str]
    voice_style: str
    role_title: str
    default_screen_time_seconds: int
    primary_image_asset_id: Optional[str]
    primary_image_url: Optional[str]
    default_background_id: Optional[str]
    is_default: bool
    references: list[CharacterReferenceResponse]
    rule: Optional[CharacterRuleResponse]
    project_usage_count: int = 0
    created_at: datetime


# --- Backgrounds ---
class BackgroundPreservationRules(BaseModel):
    keep_desk_position: bool = True
    keep_camera_angle: bool = True
    keep_monitors: bool = True
    keep_lighting: bool = True
    keep_room_layout: bool = True
    seat_character_properly: bool = True
    no_extra_objects: bool = True
    custom_rules: str = ""


class BackgroundCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    style: str = "photorealistic"
    media_asset_id: Optional[str] = None
    cloudinary_url: Optional[str] = None
    preservation_rules_json: BackgroundPreservationRules = Field(default_factory=BackgroundPreservationRules)
    default_character_id: Optional[str] = None


class BackgroundUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    style: Optional[str] = None
    media_asset_id: Optional[str] = None
    cloudinary_url: Optional[str] = None
    preservation_rules_json: Optional[BackgroundPreservationRules] = None
    default_character_id: Optional[str] = None


class BackgroundResponse(BaseModel):
    id: str
    name: str
    description: str
    style: str
    media_asset_id: Optional[str]
    image_url: Optional[str]
    cloudinary_url: Optional[str]
    preservation_rules_json: dict[str, Any]
    default_character_id: Optional[str]
    project_usage_count: int = 0
    created_at: datetime


# --- Projects ---
class CharacterScheduleSlot(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(gt=0)
    character_id: Optional[str] = None
    background_id: Optional[str] = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    topic_category: str = "education"
    topic: str = Field(min_length=1, max_length=1000)
    goal_description: str = ""
    language: Optional[str] = None
    target_duration_seconds: int = Field(default=60, ge=4, le=600)
    aspect_ratio: str = Field(default="9:16", pattern="^(9:16|16:9|1:1)$")
    export_quality: str = "1080p"
    visual_style: str = "photorealistic"
    default_character_id: Optional[str] = None
    default_background_id: Optional[str] = None
    image_model: str = "gemini-3.1-flash-image-portrait-2k"
    video_model: str = "omni-1.2-flash-8s-portrait"
    voice_mode: Optional[str] = Field(default=None, pattern="^(ai_generated|external_tts|silent)$")
    script_mode: Optional[str] = Field(default=None, pattern="^(ai_presenter|literal)$")
    voice_description: Optional[str] = Field(default=None, max_length=500)
    video_count: int = Field(default=1, ge=1, le=200)
    global_rules: str = (
        "Maintain a consistent character appearance, clothing, lighting, and photorealistic camera continuity across all scenes. "
        "Static camera on narration. No background music unless requested."
    )
    character_schedule_json: list[CharacterScheduleSlot] = Field(default_factory=list)
    auto_approve_master: bool = False


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    topic_category: Optional[str] = None
    topic: Optional[str] = Field(default=None, max_length=1000)
    goal_description: Optional[str] = None
    language: Optional[str] = None
    target_duration_seconds: Optional[int] = Field(default=None, ge=4, le=600)
    aspect_ratio: Optional[str] = Field(default=None, pattern="^(9:16|16:9|1:1)$")
    export_quality: Optional[str] = None
    visual_style: Optional[str] = None
    default_character_id: Optional[str] = None
    default_background_id: Optional[str] = None
    image_model: Optional[str] = None
    video_model: Optional[str] = None
    voice_mode: Optional[str] = Field(default=None, pattern="^(ai_generated|external_tts|silent)$")
    script_mode: Optional[str] = Field(default=None, pattern="^(ai_presenter|literal)$")
    voice_description: Optional[str] = Field(default=None, max_length=500)
    video_count: Optional[int] = Field(default=None, ge=1, le=200)
    global_rules: Optional[str] = None
    character_schedule_json: Optional[list[CharacterScheduleSlot]] = None
    auto_approve_master: Optional[bool] = None
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    topic_category: str
    topic: str
    goal_description: str
    language: str
    target_duration_seconds: int
    aspect_ratio: str
    export_quality: str
    visual_style: str
    default_character_id: Optional[str]
    default_background_id: Optional[str]
    image_model: str
    video_model: str
    voice_mode: str
    script_mode: str = "ai_presenter"
    voice_description: str = ""
    video_count: int
    global_rules: str
    character_schedule_json: list[dict[str, Any]]
    timeline_config_json: dict[str, Any]
    auto_approve_master: bool
    status: str
    scene_count: int = 0
    ready_images_count: int = 0
    ready_videos_count: int = 0
    completed_renders_count: int = 0
    created_at: datetime
    updated_at: datetime


# --- Scripts & Scenes & Prompts ---
class ScriptGenerateRequest(BaseModel):
    project_id: str
    topic_override: Optional[str] = None
    target_duration_seconds: Optional[int] = Field(default=None, ge=4, le=600)
    force_overwrite_locked: bool = False


class SceneCreateRequest(BaseModel):
    title: str = "New Scene"
    planned_duration_sec: float = Field(default=8.0, ge=2.0, le=30.0)
    character_id: Optional[str] = None
    background_id: Optional[str] = None
    dialogue: str = ""
    caption_text: str = ""
    character_action: str = "Presenter looking directly at camera with subtle hand gestures."
    camera_motion: str = "Static eye-level medium shot"
    transition_type: str = "none"


class SceneUpdateRequest(BaseModel):
    title: Optional[str] = None
    planned_duration_sec: Optional[float] = Field(default=None, ge=2.0, le=30.0)
    trim_start_sec: Optional[float] = Field(default=None, ge=0.0)
    trim_end_sec: Optional[float] = Field(default=None, ge=0.0)
    character_id: Optional[str] = None
    background_id: Optional[str] = None
    dialogue: Optional[str] = None
    caption_text: Optional[str] = None
    character_action: Optional[str] = None
    camera_motion: Optional[str] = None
    transition_type: Optional[str] = None
    is_locked: Optional[bool] = None


class ScenePromptUpdateRequest(BaseModel):
    prompt_type: str = Field(pattern="^(image|video)$")
    layers_json: Optional[dict[str, str]] = None
    locked_layers_json: Optional[list[str]] = None
    compiled_prompt: Optional[str] = None
    is_manual_override: Optional[bool] = None
    rebuild_from_rules: bool = False


class SceneAssetInfo(BaseModel):
    id: str
    media_asset_id: str
    asset_role: str
    version: int
    is_active: bool
    flow_media_id: Optional[str]
    url: str
    width: Optional[int]
    height: Optional[int]
    duration_seconds: Optional[float]
    continuity_metadata_json: dict[str, Any]
    created_at: datetime


class ScenePromptInfo(BaseModel):
    id: str
    prompt_type: str
    version: int
    is_active: bool
    layers_json: dict[str, str]
    locked_layers_json: list[str]
    compiled_prompt: str
    is_manual_override: bool


class SceneResponse(BaseModel):
    id: str
    project_id: str
    script_id: str
    scene_index: int
    title: str
    start_time_sec: float
    end_time_sec: float
    planned_duration_sec: float
    actual_duration_sec: Optional[float]
    trim_start_sec: float
    trim_end_sec: Optional[float]
    character_id: Optional[str]
    character_name: Optional[str]
    background_id: Optional[str]
    background_name: Optional[str]
    dialogue: str
    caption_text: str
    estimated_speech_sec: float
    speech_warning: Optional[str]
    character_action: str
    camera_motion: str
    transition_type: str
    is_locked: bool
    is_master_scene: bool
    approval_status: str
    continuity_status: str
    continuity_notes: Optional[str]
    status: str
    image_url: Optional[str] = None
    video_url: Optional[str] = None
    prompts: list[ScenePromptInfo] = Field(default_factory=list)
    assets: list[SceneAssetInfo] = Field(default_factory=list)


class ScriptResponse(BaseModel):
    id: str
    project_id: str
    version: int
    is_active: bool
    title: str
    objective: str
    opening_hook: str
    narration_summary: str
    total_estimated_seconds: float
    llm_provider_used: str
    status: str
    scenes: list[SceneResponse] = Field(default_factory=list)
    created_at: datetime


class GenerateSceneMediaRequest(BaseModel):
    model_override: Optional[str] = None


# --- Timeline ---
class TimelineSceneOrderItem(BaseModel):
    scene_id: str
    trim_start_sec: Optional[float] = None
    trim_end_sec: Optional[float] = None
    transition_type: Optional[str] = None
    character_id: Optional[str] = None
    background_id: Optional[str] = None


class TimelineUpdateRequest(BaseModel):
    script_id: Optional[str] = None
    scenes: list[TimelineSceneOrderItem]
    timeline_config_json: dict[str, Any] = Field(default_factory=dict)


# --- Jobs & Renders & Batches ---
class GenerationAttemptResponse(BaseModel):
    id: str
    attempt_number: int
    model_used: str
    prompt_sent: str
    reference_summary_json: list[str]
    http_status: Optional[int]
    upstream_error_code: Optional[str]
    error_message: Optional[str]
    response_excerpt: Optional[str]
    duration_ms: int
    status: str
    created_at: datetime


class GenerationJobResponse(BaseModel):
    id: str
    project_id: str
    batch_id: Optional[str]
    scene_id: Optional[str]
    render_id: Optional[str]
    job_type: str
    provider_model: str
    status: str
    progress_percent: int
    status_message: str
    attempt_count: int
    max_attempts: int
    error_code: Optional[str]
    error_message: Optional[str]
    result_json: dict[str, Any]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    attempts: list[GenerationAttemptResponse] = Field(default_factory=list)


class RenderCreateRequest(BaseModel):
    project_id: str
    script_id: Optional[str] = None
    render_config_override: Optional[dict[str, Any]] = None


class RenderResponse(BaseModel):
    id: str
    project_id: str
    project_name: Optional[str] = None
    version: int
    status: str
    duration_seconds: float
    file_size_bytes: int
    resolution: str
    mp4_url: Optional[str] = None
    srt_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    error_message: Optional[str] = None
    ffmpeg_log_excerpt: Optional[str] = None
    created_at: datetime


class BatchCreateRequest(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=255)
    total_videos: int = Field(default=5, ge=1, le=200)
    topic_variants: list[str] = Field(default_factory=list)
    concurrency_limit: int = Field(default=1, ge=1, le=5)
    request_delay_seconds: int = Field(default=3, ge=0, le=120)


class BatchItemResponse(BaseModel):
    id: str
    item_index: int
    title: str
    topic_variant: str
    script_id: Optional[str]
    render_id: Optional[str]
    status: str
    error_message: Optional[str]


class BatchResponse(BaseModel):
    id: str
    project_id: str
    project_name: Optional[str] = None
    name: str
    total_videos: int
    completed_videos: int
    failed_videos: int
    concurrency_limit: int
    request_delay_seconds: int
    status: str
    error_summary: Optional[str]
    items: list[BatchItemResponse] = Field(default_factory=list)
    created_at: datetime


# --- Provider Settings ---
class ProviderSettingUpdateRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # Only updated if provided and non-empty
    model_default: Optional[str] = None
    config_json: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ProviderSettingResponse(BaseModel):
    provider_key: str
    base_url: Optional[str]
    has_api_key: bool
    api_key_masked: str
    model_default: Optional[str]
    config_json: dict[str, Any]
    is_active: bool
    updated_at: Optional[datetime] = None


class ProviderTestRequest(BaseModel):
    provider_key: str = Field(pattern="^(flow2api|script_llm|cloudinary|r2)$")
