import base64
import hashlib
import hmac
import ipaddress
import secrets
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db

bearer_scheme = HTTPBearer(auto_error=False)

ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
ALLOWED_VIDEO_MIMES = {"video/mp4", "video/webm", "video/quicktime"}
ALLOWED_AUDIO_MIMES = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/ogg"}
ALLOWED_SUBTITLE_MIMES = {"application/x-subrip", "text/plain", "text/vtt"}


def hash_password(password: str) -> str:
    """Hash a plaintext password with per-user salt using scrypt."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=16384,
        r=8,
        p=1,
        dklen=32,
    )
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored scrypt hash."""
    try:
        scheme, salt_b64, digest_b64 = stored_hash.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            dklen=len(expected),
        )
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def create_access_token(user_id: str, email: str, role: str) -> str:
    """Create a signed HS256 JWT access token."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT access token."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
        ) from exc


def _derive_stream_key(secret: str, nonce: bytes, counter: int) -> bytes:
    return hashlib.sha256(secret.encode("utf-8") + nonce + counter.to_bytes(4, "big")).digest()


def encrypt_secret(plaintext: str) -> str:
    """Encrypt an upstream API secret using SHA-256 CTR + HMAC-SHA256 authentication."""
    if not plaintext:
        return ""
    settings = get_settings()
    secret = settings.settings_encryption_secret
    nonce = secrets.token_bytes(16)
    raw = plaintext.encode("utf-8")
    out = bytearray(len(raw))
    block_idx = 0
    offset = 0
    while offset < len(raw):
        keystream = _derive_stream_key(secret, nonce, block_idx)
        chunk = raw[offset : offset + len(keystream)]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ keystream[i]
        offset += len(keystream)
        block_idx += 1
    ciphertext = bytes(out)
    mac = hmac.new(secret.encode("utf-8"), nonce + ciphertext, hashlib.sha256).digest()
    blob = nonce + mac + ciphertext
    return "enc:" + base64.urlsafe_b64encode(blob).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored API secret encrypted by encrypt_secret."""
    if not token:
        return ""
    if not token.startswith("enc:"):
        return token
    settings = get_settings()
    secret = settings.settings_encryption_secret
    raw_blob = base64.urlsafe_b64decode(token[4:].encode("ascii"))
    if len(raw_blob) < 48:
        raise ValueError("Corrupted encrypted secret")
    nonce = raw_blob[:16]
    mac = raw_blob[16:48]
    ciphertext = raw_blob[48:]
    expected_mac = hmac.new(secret.encode("utf-8"), nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("Secret integrity verification failed")
    out = bytearray(len(ciphertext))
    block_idx = 0
    offset = 0
    while offset < len(ciphertext):
        keystream = _derive_stream_key(secret, nonce, block_idx)
        chunk = ciphertext[offset : offset + len(keystream)]
        for i, b in enumerate(chunk):
            out[offset + i] = b ^ keystream[i]
        offset += len(keystream)
        block_idx += 1
    return bytes(out).decode("utf-8")


def mask_secret(value: str) -> str:
    """Mask an API secret for safe API responses and UI display."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}***{value[-4:]}"


def sign_media_token(asset_id: str, expires_in: int = 86400) -> tuple[str, int]:
    """Generate an HMAC signature and expiry epoch for local media access."""
    settings = get_settings()
    exp = int(time.time()) + expires_in
    msg = f"{asset_id}:{exp}".encode("utf-8")
    sig = hmac.new(settings.media_signing_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return sig, exp


def verify_media_token(asset_id: str, sig: str, exp: int) -> bool:
    """Verify HMAC signature and expiry timestamp for local media access."""
    if int(time.time()) > exp:
        return False
    settings = get_settings()
    msg = f"{asset_id}:{exp}".encode("utf-8")
    expected = hmac.new(settings.media_signing_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def detect_mime_from_magic(data: bytes) -> Optional[str]:
    """Detect real MIME type from binary magic headers."""
    if len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"qt  ", b"mqt "):
            return "video/quicktime"
        if brand in (b"M4A ", b"m4a "):
            return "audio/mp4"
        return "video/mp4"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:4] == b"OggS":
        return "audio/ogg"
    return None


def validate_media_bytes(data: bytes, expected_kind: str) -> str:
    """Validate uploaded/imported file size and real magic MIME type."""
    if not data:
        raise HTTPException(status_code=400, detail="Empty file payload")
    max_bytes = 20 * 1024 * 1024 if expected_kind == "image" else 250 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum allowed size ({max_bytes // (1024 * 1024)} MB)",
        )
    detected = detect_mime_from_magic(data)
    if expected_kind == "image":
        if detected not in ALLOWED_IMAGE_MIMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid image format (detected={detected or 'unknown'}). Allowed: PNG, JPEG, WEBP, GIF.",
            )
        return detected
    if expected_kind == "video":
        if detected not in ALLOWED_VIDEO_MIMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid video format (detected={detected or 'unknown'}). Allowed: MP4, WEBM, MOV.",
            )
        return detected
    if expected_kind == "audio":
        if detected not in ALLOWED_AUDIO_MIMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid audio format (detected={detected or 'unknown'}). Allowed: MP3, WAV, M4A, OGG.",
            )
        return detected
    raise HTTPException(status_code=400, detail=f"Unsupported asset_kind: {expected_kind}")


def validate_external_url(url: str) -> str:
    """Block SSRF by verifying public http/https scheme and non-private resolved IPs."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://")
    hostname = parsed.hostname.lower()
    if hostname in ("localhost", "localhost.localdomain"):
        raise HTTPException(status_code=400, detail="Importing from localhost is forbidden")
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail=f"Unable to resolve hostname: {hostname}") from exc
    if not infos:
        raise HTTPException(status_code=400, detail="Hostname did not resolve to any IP address")
    for info in infos:
        ip_str = info[4][0].split("%")[0]
        ip_obj = ipaddress.ip_address(ip_str)
        if not ip_obj.is_global or ip_obj.is_multicast or ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
            raise HTTPException(
                status_code=400,
                detail=f"Blocked non-public destination IP ({ip_str}) for SSRF protection",
            )
    return url.strip()


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    token_query: Optional[str] = Query(default=None, alias="token"),
    db: Session = Depends(get_db),
):
    """Resolve authenticated User from Bearer header or query token (for SSE EventSource)."""
    from app.models.entities import User

    raw_token = credentials.credentials if credentials else token_query
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    payload = decode_access_token(raw_token)
    user_id = payload.get("sub")
    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive",
        )
    return user

