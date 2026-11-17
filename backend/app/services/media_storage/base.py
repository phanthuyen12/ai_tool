import logging
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class StorageUploadResult:
    storage_backend: str
    storage_key: str
    public_url: str | None
    file_size_bytes: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)


class StorageAdapter(Protocol):
    name: str

    def upload_bytes(
        self, data: bytes, key: str, mime_type: str, asset_kind: str = "image"
    ) -> StorageUploadResult: ...

    def upload_file(
        self, file_path: Path, key: str, mime_type: str, asset_kind: str = "video"
    ) -> StorageUploadResult: ...

    def read_bytes(self, storage_key: str) -> bytes: ...

    def delete(self, storage_key: str) -> None: ...


class TempWorkspaceManager:
    """Isolated temporary scratch directory that always deletes itself on exit."""

    def __init__(self, prefix: str = "job"):
        settings = get_settings()
        self.root = Path(settings.temp_workspace_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{prefix}_{uuid.uuid4().hex[:12]}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if self.path.exists():
                shutil.rmtree(self.path, ignore_errors=True)
        except Exception as exc:
            logger.warning("Failed to clean temp workspace %s: %s", self.path, exc)


def cleanup_stale_temp_dirs(max_age_seconds: int | None = None) -> int:
    """Remove leftover temporary directories older than max_age_seconds."""
    settings = get_settings()
    root = Path(settings.temp_workspace_dir)
    if not root.exists():
        return 0
    ttl = max_age_seconds if max_age_seconds is not None else settings.temp_max_age_seconds
    now = time.time()
    removed = 0
    for entry in root.iterdir():
        try:
            age = now - entry.stat().st_mtime
            if age >= ttl:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                removed += 1
        except Exception as exc:
            logger.warning("Failed removing stale temp entry %s: %s", entry, exc)
    return removed


def get_disk_status() -> dict[str, Any]:
    """Return disk space metrics for the storage & temp workspace path."""
    settings = get_settings()
    target = Path(settings.local_storage_dir)
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    free_gb = round(usage.free / (1024**3), 2)
    total_gb = round(usage.total / (1024**3), 2)
    used_gb = round(usage.used / (1024**3), 2)
    return {
        "total_gb": total_gb,
        "used_gb": used_gb,
        "free_gb": free_gb,
        "min_required_gb": settings.min_free_disk_gb,
        "healthy": free_gb >= settings.min_free_disk_gb,
    }

