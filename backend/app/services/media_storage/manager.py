import base64
import io
import json
import logging
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import MediaAsset
from app.services.media_storage.base import TempWorkspaceManager
from app.services.media_storage.cloudinary_adapter import CloudinaryStorageAdapter
from app.services.media_storage.local_adapter import LocalStorageAdapter
from app.services.media_storage.r2_adapter import R2StorageAdapter
from app.services.provider_config import EffectiveRuntimeConfig, get_effective_runtime_config

logger = logging.getLogger(__name__)

MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mp4": "m4a",
    "audio/ogg": "ogg",
    "application/x-subrip": "srt",
    "text/plain": "txt",
}


class MediaStorageService:
    """Facade selecting Cloudinary, R2, or LocalStorageAdapter for persistent media."""

    def __init__(self, runtime_cfg: Optional[EffectiveRuntimeConfig] = None):
        self.settings = get_settings()
        self.cfg = runtime_cfg
        self.local = LocalStorageAdapter(self.settings.local_storage_dir)

    @classmethod
    def from_db(cls, db: Session, owner_id: Optional[str] = None) -> "MediaStorageService":
        return cls(get_effective_runtime_config(db, owner_id=owner_id))

    def _cloudinary(self) -> Optional[CloudinaryStorageAdapter]:
        if not self.cfg:
            return None
        adapter = CloudinaryStorageAdapter(
            cloud_name=self.cfg.cloudinary_cloud_name,
            api_key=self.cfg.cloudinary_api_key,
            api_secret=self.cfg.cloudinary_api_secret,
            folder=self.cfg.cloudinary_folder,
        )
        return adapter if adapter.is_configured else None

    def _r2(self) -> Optional[R2StorageAdapter]:
        if not self.cfg:
            return None
        adapter = R2StorageAdapter(
            endpoint_url=self.cfg.r2_endpoint_url,
            access_key_id=self.cfg.r2_access_key_id,
            secret_access_key=self.cfg.r2_secret_access_key,
            bucket_name=self.cfg.r2_bucket_name,
            public_base_url=self.cfg.r2_public_base_url,
        )
        return adapter if adapter.is_configured else None

    def _pick_adapter(self, asset_kind: str):
        if asset_kind == "image":
            if self.cfg and self.cfg.storage_image_backend == "cloudinary":
                if cld := self._cloudinary():
                    return cld
            if self.cfg and self.cfg.storage_image_backend == "r2":
                if r2 := self._r2():
                    return r2
            return self.local
        if asset_kind in ("video", "audio", "subtitle"):
            if self.cfg and self.cfg.storage_video_backend == "r2":
                if r2 := self._r2():
                    return r2
            if self.cfg and self.cfg.storage_video_backend == "cloudinary" and asset_kind == "video":
                if cld := self._cloudinary():
                    return cld
            return self.local
        return self.local

    @staticmethod
    def _build_storage_key(asset_kind: str, mime_type: str, filename: str) -> str:
        ext = MIME_TO_EXT.get(mime_type) or Path(filename).suffix.lstrip(".") or "bin"
        ym = datetime.now(timezone.utc).strftime("%Y_%m")
        return f"{asset_kind}/{ym}/{uuid.uuid4().hex}.{ext}"

    @staticmethod
    def _inspect_image_bytes(data: bytes) -> tuple[Optional[int], Optional[int]]:
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = ImageOps.exif_transpose(img)
                return img.width, img.height
        except Exception:
            return None, None

    @staticmethod
    def probe_video_file(file_path: Path) -> dict:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        try:
            out = subprocess.check_output(cmd, timeout=30, stderr=subprocess.DEVNULL)
            parsed = json.loads(out.decode("utf-8"))
            fmt = parsed.get("format", {})
            streams = parsed.get("streams", [])
            v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
            a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
            duration = float(fmt.get("duration") or v_stream.get("duration") or 0.0)
            return {
                "duration": round(duration, 3) if duration else None,
                "width": v_stream.get("width"),
                "height": v_stream.get("height"),
                "has_audio": a_stream is not None,
                "video_codec": v_stream.get("codec_name"),
                "audio_codec": a_stream.get("codec_name") if a_stream else None,
            }
        except Exception as exc:
            logger.warning("ffprobe failed for %s: %s", file_path, exc)
            return {}

    def save_bytes(
        self,
        db: Session,
        *,
        owner_id: str,
        data: bytes,
        original_filename: str,
        mime_type: str,
        asset_kind: str,
        project_id: Optional[str] = None,
        extra_metadata: Optional[dict] = None,
    ) -> MediaAsset:
        adapter = self._pick_adapter(asset_kind)
        key = self._build_storage_key(asset_kind, mime_type, original_filename)
        upload = adapter.upload_bytes(data=data, key=key, mime_type=mime_type, asset_kind=asset_kind)

        width = height = duration = None
        probe_meta: dict = {}
        if asset_kind == "image":
            width, height = self._inspect_image_bytes(data)
        elif asset_kind in ("video", "audio"):
            if upload.storage_backend == "local":
                probe_meta = self.probe_video_file(self.local.resolve_path(upload.storage_key))
            else:
                with TempWorkspaceManager(prefix="probe") as tmp_dir:
                    tmp_file = tmp_dir / Path(key).name
                    tmp_file.write_bytes(data)
                    probe_meta = self.probe_video_file(tmp_file)
            width = probe_meta.get("width")
            height = probe_meta.get("height")
            duration = probe_meta.get("duration")

        merged_meta = {**upload.metadata, **probe_meta, **(extra_metadata or {})}
        asset = MediaAsset(
            owner_id=owner_id,
            project_id=project_id,
            storage_backend=upload.storage_backend,
            storage_key=upload.storage_key,
            public_url=upload.public_url,
            original_filename=original_filename,
            mime_type=mime_type,
            asset_kind=asset_kind,
            file_size_bytes=upload.file_size_bytes,
            width=width,
            height=height,
            duration_seconds=duration,
            sha256=upload.sha256,
            metadata_json=merged_meta,
        )
        db.add(asset)
        db.flush()
        return asset

    def save_file(
        self,
        db: Session,
        *,
        owner_id: str,
        file_path: Path,
        original_filename: str,
        mime_type: str,
        asset_kind: str,
        project_id: Optional[str] = None,
        extra_metadata: Optional[dict] = None,
    ) -> MediaAsset:
        adapter = self._pick_adapter(asset_kind)
        key = self._build_storage_key(asset_kind, mime_type, original_filename)
        upload = adapter.upload_file(file_path=file_path, key=key, mime_type=mime_type, asset_kind=asset_kind)

        width = height = duration = None
        probe_meta: dict = {}
        if asset_kind == "image":
            width, height = self._inspect_image_bytes(file_path.read_bytes())
        elif asset_kind in ("video", "audio"):
            probe_meta = self.probe_video_file(file_path)
            width = probe_meta.get("width")
            height = probe_meta.get("height")
            duration = probe_meta.get("duration")

        merged_meta = {**upload.metadata, **probe_meta, **(extra_metadata or {})}
        asset = MediaAsset(
            owner_id=owner_id,
            project_id=project_id,
            storage_backend=upload.storage_backend,
            storage_key=upload.storage_key,
            public_url=upload.public_url,
            original_filename=original_filename,
            mime_type=mime_type,
            asset_kind=asset_kind,
            file_size_bytes=upload.file_size_bytes,
            width=width,
            height=height,
            duration_seconds=duration,
            sha256=upload.sha256,
            metadata_json=merged_meta,
        )
        db.add(asset)
        db.flush()
        return asset

    def resolve_access_url(self, asset: MediaAsset) -> str:
        """Return signed local URL or cloud public URL for a MediaAsset."""
        if asset.storage_backend == "local" or not asset.public_url:
            return LocalStorageAdapter.build_signed_url(asset.id)
        return asset.public_url

    def read_asset_bytes(self, asset: MediaAsset) -> bytes:
        if asset.storage_backend == "local":
            return self.local.read_bytes(asset.storage_key)
        if asset.storage_backend == "r2":
            if r2 := self._r2():
                return r2.read_bytes(asset.storage_key)
        if asset.public_url:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                resp = client.get(asset.public_url)
                resp.raise_for_status()
                return resp.content
        raise FileNotFoundError(f"Cannot read bytes for media asset {asset.id}")

    def copy_asset_to_path(self, asset: MediaAsset, dest_path: Path) -> Path:
        if asset.storage_backend == "local":
            src = self.local.resolve_path(asset.storage_key)
            dest_path.write_bytes(src.read_bytes())
            return dest_path
        data = self.read_asset_bytes(asset)
        dest_path.write_bytes(data)
        return dest_path

    def prepare_reference_for_flow2api(self, asset: MediaAsset) -> str:
        """Return public HTTPS URL (if <=2048px) or resized <=2048px base64 data URI for Flow2API."""
        if asset.public_url and asset.public_url.startswith("https://") and asset.storage_backend != "cloudinary":
            parsed = urlparse(asset.public_url)
            host = (parsed.hostname or "").lower()
            if host not in ("localhost", "127.0.0.1"):
                if (asset.width or 0) <= 2048 and (asset.height or 0) <= 2048:
                    return asset.public_url

        raw = self.read_asset_bytes(asset)
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "LA", "P"):
                canvas = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                canvas.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
                img = canvas
            elif img.mode != "RGB":
                img = img.convert("RGB")

            if max(img.width, img.height) > 2048:
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=90, optimize=True)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
