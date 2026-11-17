import hashlib
import time
from pathlib import Path

import httpx

from app.services.media_storage.base import StorageUploadResult


class CloudinaryStorageAdapter:
    """Lightweight REST client for Cloudinary signed uploads (no heavy SDK needed)."""

    name = "cloudinary"

    def __init__(self, cloud_name: str, api_key: str, api_secret: str, folder: str = "ai-video-studio"):
        self.cloud_name = cloud_name.strip()
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.folder = folder.strip() or "ai-video-studio"

    @property
    def is_configured(self) -> bool:
        return bool(self.cloud_name and self.api_key and self.api_secret)

    def _sign_params(self, params: dict[str, str]) -> str:
        to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
        raw = f"{to_sign}{self.api_secret}".encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def upload_bytes(
        self, data: bytes, key: str, mime_type: str, asset_kind: str = "image"
    ) -> StorageUploadResult:
        if not self.is_configured:
            raise RuntimeError("Cloudinary credentials are not configured")
        resource_type = "video" if asset_kind in ("video", "audio") else "image"
        timestamp = str(int(time.time()))
        public_id = key.rsplit(".", 1)[0]
        sign_payload = {"folder": self.folder, "public_id": public_id, "timestamp": timestamp}
        signature = self._sign_params(sign_payload)
        endpoint = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/{resource_type}/upload"
        filename = Path(key).name
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                endpoint,
                data={
                    "api_key": self.api_key,
                    "timestamp": timestamp,
                    "folder": self.folder,
                    "public_id": public_id,
                    "signature": signature,
                },
                files={"file": (filename, data, mime_type)},
            )
        resp.raise_for_status()
        body = resp.json()
        digest = hashlib.sha256(data).hexdigest()
        return StorageUploadResult(
            storage_backend=self.name,
            storage_key=body.get("public_id", public_id),
            public_url=body["secure_url"],
            file_size_bytes=body.get("bytes", len(data)),
            sha256=digest,
            metadata={"cloudinary": {"public_id": body.get("public_id"), "version": body.get("version")}},
        )

    def upload_file(
        self, file_path: Path, key: str, mime_type: str, asset_kind: str = "video"
    ) -> StorageUploadResult:
        return self.upload_bytes(file_path.read_bytes(), key=key, mime_type=mime_type, asset_kind=asset_kind)

    def read_bytes(self, storage_key: str) -> bytes:
        raise NotImplementedError("Cloudinary assets should be fetched via public_url")

    def delete(self, storage_key: str) -> None:
        if not self.is_configured:
            return
        timestamp = str(int(time.time()))
        signature = self._sign_params({"public_id": storage_key, "timestamp": timestamp})
        endpoint = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/image/destroy"
        with httpx.Client(timeout=30.0) as client:
            client.post(
                endpoint,
                data={
                    "public_id": storage_key,
                    "api_key": self.api_key,
                    "timestamp": timestamp,
                    "signature": signature,
                },
            )

