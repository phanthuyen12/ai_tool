from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Application settings loaded from environment variables and root .env file."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    public_base_url: str = "http://localhost:3000"
    internal_api_url: str = "http://127.0.0.1:8000"

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_video_studio"
    redis_url: str = "redis://127.0.0.1:6379/0"

    jwt_secret_key: str = "dev-jwt-secret-key-change-before-production-32chars"
    jwt_expire_minutes: int = 1440
    media_signing_secret: str = "dev-media-signing-secret-change-before-production"
    settings_encryption_secret: str = "dev-settings-encryption-secret-change-before-prod"

    admin_email: str = "admin@aivideostudio.app"
    admin_password: str = "ChangeMe123!"
    admin_full_name: str = "Studio Admin"

    flow2api_base_url: str = "https://flow.aetrading.vn/v1"
    flow2api_api_key: str = ""
    flow2api_image_timeout: int = Field(default=180, ge=30, le=1800)
    flow2api_video_timeout: int = Field(default=900, ge=60, le=3600)

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
    local_storage_dir: str = str(ROOT_DIR / "backend" / "storage_local")

    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    cloudinary_folder: str = "ai-video-studio"

    r2_endpoint_url: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "ai-video-studio"
    r2_public_base_url: str = ""

    worker_video_concurrency: int = Field(default=1, ge=1, le=10)
    worker_image_concurrency: int = Field(default=2, ge=1, le=10)
    worker_request_delay_seconds: int = Field(default=3, ge=0, le=120)
    temp_workspace_dir: str = "/tmp/ai-video-studio"
    temp_max_age_seconds: int = Field(default=7200, ge=300)
    min_free_disk_gb: float = Field(default=2.0, ge=0.2)
    ffmpeg_threads: int = Field(default=2, ge=1, le=16)
    ffmpeg_preset: str = "veryfast"
    ffmpeg_crf: int = Field(default=23, ge=16, le=32)

    @property
    def normalized_flow2api_base_url(self) -> str:
        return normalize_v1_base_url(self.flow2api_base_url)


def normalize_v1_base_url(url: Optional[str]) -> str:
    """Ensure OpenAI-compatible base URL ends with /v1 as required by Flow2API."""
    cleaned = (url or "https://flow.aetrading.vn/v1").strip().rstrip("/")
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


@lru_cache
def get_settings() -> Settings:
    return Settings()

