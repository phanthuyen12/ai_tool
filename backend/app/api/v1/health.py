import subprocess

import redis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services.media_storage.base import get_disk_status

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health_check(db: Session = Depends(get_db)):
    settings = get_settings()
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    redis_ok = False
    try:
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        redis_ok = bool(r.ping())
        r.close()
    except Exception:
        redis_ok = False

    ffmpeg_ok = False
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=3, check=True)
        ffmpeg_ok = True
    except Exception:
        ffmpeg_ok = False

    disk = get_disk_status()
    overall = "ok" if (db_ok and redis_ok and ffmpeg_ok and disk["healthy"]) else "degraded"
    return {
        "status": overall,
        "database": "ok" if db_ok else "error",
        "redis": "ok" if redis_ok else "error",
        "ffmpeg": "ok" if ffmpeg_ok else "error",
        "disk": disk,
    }

