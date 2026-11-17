import hashlib
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from app.services.media_storage.base import StorageUploadResult


class R2StorageAdapter:
    """Cloudflare R2 (S3-compatible) persistent storage adapter for MP4/SRT/media."""

    name = "r2"

    def __init__(
        self,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket_name: str,
        public_base_url: str = "",
    ):
        self.endpoint_url = endpoint_url.strip().rstrip("/")
        self.access_key_id = access_key_id.strip()
        self.secret_access_key = secret_access_key.strip()
        self.bucket_name = bucket_name.strip()
        self.public_base_url = public_base_url.strip().rstrip("/")

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint_url and self.access_key_id and self.secret_access_key and self.bucket_name)

    def _client(self):
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )

    def _resolve_url(self, key: str) -> str:
        if self.public_base_url:
            return f"{self.public_base_url}/{key.lstrip('/')}"
        return self._client().generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": key},
            ExpiresIn=86400 * 7,
        )

    def upload_bytes(
        self, data: bytes, key: str, mime_type: str, asset_kind: str = "video"
    ) -> StorageUploadResult:
        if not self.is_configured:
            raise RuntimeError("Cloudflare R2 credentials are not configured")
        client = self._client()
        client.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=data,
            ContentType=mime_type,
        )
        digest = hashlib.sha256(data).hexdigest()
        return StorageUploadResult(
            storage_backend=self.name,
            storage_key=key,
            public_url=self._resolve_url(key),
            file_size_bytes=len(data),
            sha256=digest,
            metadata={"bucket": self.bucket_name, "asset_kind": asset_kind},
        )

    def upload_file(
        self, file_path: Path, key: str, mime_type: str, asset_kind: str = "video"
    ) -> StorageUploadResult:
        if not self.is_configured:
            raise RuntimeError("Cloudflare R2 credentials are not configured")
        hasher = hashlib.sha256()
        with file_path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                hasher.update(chunk)
        client = self._client()
        client.upload_file(
            str(file_path),
            self.bucket_name,
            key,
            ExtraArgs={"ContentType": mime_type},
        )
        return StorageUploadResult(
            storage_backend=self.name,
            storage_key=key,
            public_url=self._resolve_url(key),
            file_size_bytes=file_path.stat().st_size,
            sha256=hasher.hexdigest(),
            metadata={"bucket": self.bucket_name, "asset_kind": asset_kind},
        )

    def read_bytes(self, storage_key: str) -> bytes:
        client = self._client()
        obj = client.get_object(Bucket=self.bucket_name, Key=storage_key)
        return obj["Body"].read()

    def delete(self, storage_key: str) -> None:
        if not self.is_configured:
            return
        self._client().delete_object(Bucket=self.bucket_name, Key=storage_key)

