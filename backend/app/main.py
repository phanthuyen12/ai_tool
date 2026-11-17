from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.api.v1 import (
    auth,
    backgrounds,
    batches,
    characters,
    dashboard,
    health,
    jobs,
    media,
    projects,
    providers,
    renders,
    scenes,
    scripts,
    timeline,
)
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.core.security import hash_password
from app.models.entities import CharacterRule, User
from app.services.media_storage.base import cleanup_stale_temp_dirs

configure_logging()
logger = logging.getLogger(__name__)


def _bootstrap_defaults() -> None:
    """Seed initial admin user and default character rule presets if absent."""
    settings = get_settings()
    db = SessionLocal()
    try:
        user = db.scalars(select(User)).first()
        if not user:
            user = User(
                email=settings.admin_email.lower(),
                full_name=settings.admin_full_name,
                password_hash=hash_password(settings.admin_password),
                role="owner",
            )
            db.add(user)
            db.flush()
            logger.info("Seeded bootstrap admin account: %s", user.email)

        preset = db.scalars(
            select(CharacterRule).where(
                CharacterRule.owner_id == user.id, CharacterRule.is_preset.is_(True)
            )
        ).first()
        if not preset:
            db.add(
                CharacterRule(
                    owner_id=user.id,
                    character_id=None,
                    preset_name="Strict Studio Presenter (Default)",
                    is_preset=True,
                    preserve_face=True,
                    preserve_hair_and_age=True,
                    clothing_rule="Preserve original face and clothing exactly across every scene",
                    preserve_skin_and_identity=True,
                    natural_movement_only=True,
                    no_extra_characters=True,
                    preserve_visual_style=True,
                    hand_gesture_level="moderate",
                    static_camera_on_speech=True,
                    no_auto_bgm=True,
                    custom_directives="Maintain consistent eye-line toward camera and steady studio posture.",
                )
            )
        db.commit()
    except Exception as exc:
        logger.warning("Bootstrap seed skipped/failed (run alembic upgrade head first?): %s", exc)
        db.rollback()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cleanup_stale_temp_dirs()
    _bootstrap_defaults()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AI Video Studio API",
        version="0.1.0",
        description="Production API for batch AI video authoring with Flow2API and FFmpeg",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_base_url, "http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = "/api/v1"
    for router in (
        health.router,
        auth.router,
        dashboard.router,
        media.router,
        characters.router,
        backgrounds.router,
        projects.router,
        scripts.router,
        scenes.router,
        timeline.router,
        batches.router,
        jobs.router,
        renders.router,
        providers.router,
    ):
        app.include_router(router, prefix=prefix)

    return app


app = create_app()

