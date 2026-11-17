import hashlib
from pathlib import Path

from app.core.security import sign_media_token
from app.services.media_storage.base import StorageUploadResult


class LocalStorageAdapter:
    """Filesystem-backed persistent storage adapter for local dev & fallback."""

    name = "local"

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, storage_key: str) -> Path:
        clean_key = storage_key.lstrip("/").replace("..", "")
        candidate = (self.base_dir / clean_key).resolve()
        if self.base_dir not in candidate.parents and candidate != self.base_dir:
            raise ValueError("Invalid storage key path traversal attempt")
        return candidate

    def upload_bytes(
        self, data: bytes, key: str, mime_type: str, asset_kind: str = "image"
    ) -> StorageUploadResult:
        dest = self.resolve_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return StorageUploadResult(
            storage_backend=self.name,
            storage_key=key,
            public_url=None,
            file_size_bytes=len(data),
            sha256=digest,
            metadata={"mime_type": mime_type, "asset_kind": asset_kind},
        )

    def upload_file(
        self, file_path: Path, key: str, mime_type: str, asset_kind: str = "video"
    ) -> StorageUploadResult:
        dest = self.resolve_path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        hasher = hashlib.sha256()
        total = 0
        with file_path.open("rb") as src, dest.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
                hasher.update(chunk)
                total += len(chunk)
        return StorageUploadResult(
            storage_backend=self.name,
            storage_key=key,
            public_url=None,
            file_size_bytes=total,
            sha256=hasher.hexdigest(),
            metadata={"mime_type": mime_type, "asset_kind": asset_kind},
        )

    def read_bytes(self, storage_key: str) -> bytes:
        return self.resolve_path(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        path = self.resolve_path(storage_key)
        if path.exists():
            path.unlink()

    @staticmethod
    def build_signed_url(asset_id: str, expires_in: int = 86400) -> str:
        sig, exp = sign_media_token(asset_id, expires_in=expires_in)
        return f"/api/v1/media/files/{asset_id}?sig={sig}&exp={exp}"

