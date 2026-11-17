from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings, normalize_v1_base_url
from app.core.security import decrypt_secret
from app.models.entities import ProviderSetting


@dataclass
class EffectiveRuntimeConfig:
    flow2api_base_url: str
    flow2api_api_key: str
    flow2api_image_timeout: int
    flow2api_video_timeout: int
    flow2api_cached_models: list[dict[str, Any]] = field(default_factory=list)

    script_llm_provider: str = "openai_compatible"
    script_llm_base_url: str = "https://api.openai.com/v1"
    script_llm_api_key: str = ""
    script_llm_model: str = "gpt-4o-mini"

    caption_llm_enabled: bool = False
    caption_llm_provider: str = "openai_compatible"
    caption_llm_base_url: str = ""
    caption_llm_api_key: str = ""
    caption_llm_model: str = ""

    storage_image_backend: str = "local"
    storage_video_backend: str = "local"

    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_folder: str = "ai-video-studio"

    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "ai-video-studio"
    r2_public_base_url: str = ""

    worker_video_concurrency: int = 1
    worker_image_concurrency: int = 2
    worker_request_delay_seconds: int = 3
    default_language: str = "vi-VN"
    default_voice_mode: str = "ai_generated"
    default_voice_description: str = "Natural clear Vietnamese presenter voice"


def get_effective_runtime_config(db: Session, owner_id: Optional[str] = None) -> EffectiveRuntimeConfig:
    """Merge .env defaults with per-owner DB provider_settings."""
    s = get_settings()
    cfg = EffectiveRuntimeConfig(
        flow2api_base_url=s.normalized_flow2api_base_url,
        flow2api_api_key=s.flow2api_api_key,
        flow2api_image_timeout=s.flow2api_image_timeout,
        flow2api_video_timeout=s.flow2api_video_timeout,
        script_llm_provider=s.script_llm_provider,
        script_llm_base_url=s.script_llm_base_url,
        script_llm_api_key=s.script_llm_api_key,
        script_llm_model=s.script_llm_model,
        caption_llm_enabled=s.caption_llm_enabled,
        caption_llm_provider=s.caption_llm_provider,
        caption_llm_base_url=s.caption_llm_base_url,
        caption_llm_api_key=s.caption_llm_api_key,
        caption_llm_model=s.caption_llm_model,
        storage_image_backend=s.storage_image_backend,
        storage_video_backend=s.storage_video_backend,
        cloudinary_cloud_name=s.cloudinary_cloud_name,
        cloudinary_api_key=s.cloudinary_api_key,
        cloudinary_api_secret=s.cloudinary_api_secret,
        cloudinary_folder=s.cloudinary_folder,
        r2_endpoint_url=s.r2_endpoint_url,
        r2_access_key_id=s.r2_access_key_id,
        r2_secret_access_key=s.r2_secret_access_key,
        r2_bucket_name=s.r2_bucket_name,
        r2_public_base_url=s.r2_public_base_url,
        worker_video_concurrency=s.worker_video_concurrency,
        worker_image_concurrency=s.worker_image_concurrency,
        worker_request_delay_seconds=s.worker_request_delay_seconds,
    )

    stmt = select(ProviderSetting)
    if owner_id:
        stmt = stmt.where(ProviderSetting.owner_id == owner_id)
    rows = db.scalars(stmt).all()
    by_key = {row.provider_key: row for row in rows if row.is_active}

    if flow := by_key.get("flow2api"):
        if flow.base_url:
            cfg.flow2api_base_url = normalize_v1_base_url(flow.base_url)
        if flow.api_key_encrypted:
            cfg.flow2api_api_key = decrypt_secret(flow.api_key_encrypted)
        conf = flow.config_json or {}
        if conf.get("image_timeout"):
            cfg.flow2api_image_timeout = int(conf["image_timeout"])
        if conf.get("video_timeout"):
            cfg.flow2api_video_timeout = int(conf["video_timeout"])
        if isinstance(conf.get("cached_models"), list):
            cfg.flow2api_cached_models = conf["cached_models"]

    if llm := by_key.get("script_llm"):
        conf = llm.config_json or {}
        if conf.get("provider"):
            cfg.script_llm_provider = str(conf["provider"])
        if llm.base_url:
            cfg.script_llm_base_url = llm.base_url.strip().rstrip("/")
        if llm.api_key_encrypted:
            cfg.script_llm_api_key = decrypt_secret(llm.api_key_encrypted)
        if llm.model_default:
            cfg.script_llm_model = llm.model_default.strip()

    if cap := by_key.get("caption_llm"):
        conf = cap.config_json or {}
        if conf.get("provider"):
            cfg.caption_llm_provider = str(conf["provider"])
        if cap.base_url:
            cfg.caption_llm_base_url = cap.base_url.strip().rstrip("/")
        if cap.api_key_encrypted:
            cfg.caption_llm_api_key = decrypt_secret(cap.api_key_encrypted)
        if cap.model_default:
            cfg.caption_llm_model = cap.model_default.strip()
        if "enabled" in conf:
            cfg.caption_llm_enabled = bool(conf["enabled"])

    if cld := by_key.get("cloudinary"):
        conf = cld.config_json or {}
        if conf.get("cloud_name"):
            cfg.cloudinary_cloud_name = str(conf["cloud_name"]).strip()
        if conf.get("api_key"):
            cfg.cloudinary_api_key = str(conf["api_key"]).strip()
        if cld.api_key_encrypted:
            cfg.cloudinary_api_secret = decrypt_secret(cld.api_key_encrypted)
        if conf.get("folder"):
            cfg.cloudinary_folder = str(conf["folder"]).strip()
        if conf.get("enabled"):
            cfg.storage_image_backend = "cloudinary"

    if r2 := by_key.get("r2"):
        conf = r2.config_json or {}
        if r2.base_url:
            cfg.r2_endpoint_url = r2.base_url.strip().rstrip("/")
        if conf.get("access_key_id"):
            cfg.r2_access_key_id = str(conf["access_key_id"]).strip()
        if r2.api_key_encrypted:
            cfg.r2_secret_access_key = decrypt_secret(r2.api_key_encrypted)
        if conf.get("bucket_name"):
            cfg.r2_bucket_name = str(conf["bucket_name"]).strip()
        if conf.get("public_base_url"):
            cfg.r2_public_base_url = str(conf["public_base_url"]).strip().rstrip("/")
        if conf.get("enabled"):
            cfg.storage_video_backend = "r2"

    if wrk := by_key.get("worker"):
        conf = wrk.config_json or {}
        if conf.get("video_concurrency"):
            cfg.worker_video_concurrency = max(1, min(10, int(conf["video_concurrency"])))
        if conf.get("image_concurrency"):
            cfg.worker_image_concurrency = max(1, min(10, int(conf["image_concurrency"])))
        if conf.get("request_delay_seconds") is not None:
            cfg.worker_request_delay_seconds = max(0, min(120, int(conf["request_delay_seconds"])))
        if conf.get("default_language"):
            cfg.default_language = str(conf["default_language"])
        if conf.get("default_voice_mode") in ("ai_generated", "external_tts", "silent"):
            cfg.default_voice_mode = str(conf["default_voice_mode"])
        if conf.get("default_voice_description"):
            cfg.default_voice_description = str(conf["default_voice_description"]).strip()

    return cfg
