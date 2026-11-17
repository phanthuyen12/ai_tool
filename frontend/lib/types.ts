export interface User {
  id: string;
  email: string;
  full_name: string;
  role: string;
  created_at: string;
}

export interface MediaAsset {
  id: string;
  storage_backend: string;
  original_filename: string;
  mime_type: string;
  asset_kind: "image" | "video" | "audio" | "subtitle";
  file_size_bytes: number;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  url: string;
  public_url?: string | null;
  project_id?: string | null;
  created_at: string;
}

export interface CharacterRule {
  id: string;
  character_id?: string | null;
  preset_name?: string | null;
  is_preset: boolean;
  preserve_face: boolean;
  preserve_hair_and_age: boolean;
  clothing_rule: string;
  preserve_skin_and_identity: boolean;
  natural_movement_only: boolean;
  no_extra_characters: boolean;
  preserve_visual_style: boolean;
  hand_gesture_level: string;
  static_camera_on_speech: boolean;
  no_auto_bgm: boolean;
  custom_directives: string;
}

export interface CharacterReference {
  id: string;
  media_asset_id: string;
  angle_label: string;
  is_primary: boolean;
  sort_order: number;
  url: string;
}

export interface Character {
  id: string;
  name: string;
  description: string;
  age_appearance?: string | null;
  voice_style: string;
  role_title: string;
  default_screen_time_seconds: number;
  primary_image_asset_id?: string | null;
  primary_image_url?: string | null;
  default_background_id?: string | null;
  is_default: boolean;
  references: CharacterReference[];
  rule?: CharacterRule | null;
  project_usage_count: number;
  created_at: string;
}

export interface Background {
  id: string;
  name: string;
  description: string;
  style: string;
  media_asset_id?: string | null;
  image_url?: string | null;
  cloudinary_url?: string | null;
  preservation_rules_json: {
    keep_desk_position: boolean;
    keep_camera_angle: boolean;
    keep_monitors: boolean;
    keep_lighting: boolean;
    keep_room_layout: boolean;
    seat_character_properly: boolean;
    no_extra_objects: boolean;
    custom_rules: string;
  };
  default_character_id?: string | null;
  project_usage_count: number;
  created_at: string;
}

export interface CharacterScheduleSlot {
  start_sec: number;
  end_sec: number;
  character_id?: string | null;
  background_id?: string | null;
}

export interface Project {
  id: string;
  name: string;
  topic_category: string;
  topic: string;
  goal_description: string;
  language: string;
  target_duration_seconds: number;
  aspect_ratio: "9:16" | "16:9" | "1:1";
  export_quality: string;
  visual_style: string;
  default_character_id?: string | null;
  default_background_id?: string | null;
  image_model: string;
  video_model: string;
  voice_mode: "ai_generated" | "external_tts" | "silent";
  script_mode: "ai_presenter" | "literal";
  voice_description: string;
  video_count: number;
  global_rules: string;
  character_schedule_json: CharacterScheduleSlot[];
  timeline_config_json: Record<string, unknown>;
  auto_approve_master: boolean;
  status: string;
  scene_count: number;
  ready_images_count: number;
  ready_videos_count: number;
  completed_renders_count: number;
  created_at: string;
  updated_at: string;
}

export interface ScenePromptInfo {
  id: string;
  prompt_type: "image" | "video";
  version: number;
  is_active: boolean;
  layers_json: Record<string, string>;
  locked_layers_json: string[];
  compiled_prompt: string;
  is_manual_override: boolean;
}

export interface SceneAssetInfo {
  id: string;
  media_asset_id: string;
  asset_role: "master_image" | "continuity_image" | "scene_video" | "external_tts_audio";
  version: number;
  is_active: boolean;
  flow_media_id?: string | null;
  url: string;
  width?: number | null;
  height?: number | null;
  duration_seconds?: number | null;
  continuity_metadata_json: Record<string, unknown>;
  created_at: string;
}

export interface Scene {
  id: string;
  project_id: string;
  script_id: string;
  scene_index: number;
  title: string;
  start_time_sec: number;
  end_time_sec: number;
  planned_duration_sec: number;
  actual_duration_sec?: number | null;
  trim_start_sec: number;
  trim_end_sec?: number | null;
  character_id?: string | null;
  character_name?: string | null;
  background_id?: string | null;
  background_name?: string | null;
  dialogue: string;
  caption_text: string;
  estimated_speech_sec: number;
  speech_warning?: string | null;
  character_action: string;
  camera_motion: string;
  transition_type: string;
  is_locked: boolean;
  is_master_scene: boolean;
  approval_status: string;
  continuity_status: string;
  continuity_notes?: string | null;
  status: string;
  image_url?: string | null;
  video_url?: string | null;
  prompts: ScenePromptInfo[];
  assets: SceneAssetInfo[];
}

export interface Script {
  id: string;
  project_id: string;
  version: number;
  is_active: boolean;
  title: string;
  objective: string;
  opening_hook: string;
  narration_summary: string;
  total_estimated_seconds: number;
  llm_provider_used: string;
  status: string;
  scenes: Scene[];
  created_at: string;
}

export interface GenerationAttempt {
  id: string;
  attempt_number: number;
  model_used: string;
  prompt_sent: string;
  reference_summary_json: string[];
  http_status?: number | null;
  upstream_error_code?: string | null;
  error_message?: string | null;
  response_excerpt?: string | null;
  duration_ms: number;
  status: string;
  created_at: string;
}

export interface GenerationJob {
  id: string;
  project_id: string;
  batch_id?: string | null;
  scene_id?: string | null;
  render_id?: string | null;
  job_type: string;
  provider_model: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress_percent: number;
  status_message: string;
  attempt_count: number;
  max_attempts: number;
  error_code?: string | null;
  error_message?: string | null;
  result_json: Record<string, unknown>;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  attempts: GenerationAttempt[];
}

export interface RenderItem {
  id: string;
  project_id: string;
  project_name?: string | null;
  version: number;
  status: string;
  duration_seconds: number;
  file_size_bytes: number;
  resolution: string;
  mp4_url?: string | null;
  srt_url?: string | null;
  thumbnail_url?: string | null;
  error_message?: string | null;
  ffmpeg_log_excerpt?: string | null;
  created_at: string;
}

export interface BatchItem {
  id: string;
  item_index: number;
  title: string;
  topic_variant: string;
  script_id?: string | null;
  render_id?: string | null;
  status: string;
  error_message?: string | null;
}

export interface Batch {
  id: string;
  project_id: string;
  project_name?: string | null;
  name: string;
  total_videos: number;
  completed_videos: number;
  failed_videos: number;
  concurrency_limit: number;
  request_delay_seconds: number;
  status: string;
  error_summary?: string | null;
  items: BatchItem[];
  created_at: string;
}

export interface ProviderSetting {
  provider_key: "flow2api" | "script_llm" | "cloudinary" | "r2" | "worker";
  base_url?: string | null;
  has_api_key: boolean;
  api_key_masked: string;
  model_default?: string | null;
  config_json: Record<string, unknown>;
  is_active: boolean;
  updated_at?: string | null;
}

export interface AIModelEntry {
  id: string;
  type: "image" | "video" | "other";
  output_modalities: string[];
  description: string;
  source?: string;
}
