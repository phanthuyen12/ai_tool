import logging
import uuid
from datetime import datetime, timezone

import dramatiq
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.events import publish_job_event
from app.models.entities import GenerationJob, MediaAsset, Project, Render, Scene, Script
from app.services.generation.continuity import approve_scene_image, get_active_scene_image_asset, get_active_scene_video_asset
from app.services.video_editor.composer import aspect_ratio_matches
from app.workers.tasks import run_project_render_job, run_scene_image_job, run_scene_video_job

logger = logging.getLogger(__name__)


def _set_parent_progress(job_id: str, progress: int, message: str) -> bool:
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        if not job or job.status == "cancelled":
            return False
        job.status = "running"
        job.progress_percent = progress
        job.status_message = message
        job.heartbeat_at = datetime.now(timezone.utc)
        db.commit()
        publish_job_event(job_id, "progress", {"status": "running", "progress_percent": progress, "status_message": message})
        return True


def _new_child_job(project: Project, scene: Scene | None, job_type: str, model: str, render: Render | None = None) -> str:
    with SessionLocal() as db:
        row = GenerationJob(
            owner_id=project.owner_id,
            project_id=project.id,
            scene_id=scene.id if scene else None,
            render_id=render.id if render else None,
            job_type=job_type,
            provider_model=model,
            status="queued",
            progress_percent=0,
            status_message=f"Automatic production: {job_type}",
            max_attempts=3 if job_type != "project_render" else 1,
            idempotency_key=f"auto:{project.id}:{scene.id if scene else render.id}:{uuid.uuid4().hex[:10]}",
        )
        db.add(row)
        db.commit()
        return row.id


def _run_child(job_id: str, actor) -> None:
    actor.fn(job_id)
    with SessionLocal() as db:
        child = db.get(GenerationJob, job_id)
        if not child or child.status != "completed":
            state = child.status if child else "missing"
            error = child.error_message if child else "Job record disappeared"
            raise RuntimeError(f"{child.job_type if child else 'Generation'} {state}: {error or child.status_message if child else error}")


@dramatiq.actor(max_retries=0, time_limit=7_200_000)
def run_project_auto_production(parent_job_id: str) -> None:
    """Generate missing scene images/videos in continuity order, then compose one MP4."""
    with SessionLocal() as db:
        parent = db.get(GenerationJob, parent_job_id)
        if not parent or parent.status == "cancelled":
            return
        project = db.get(Project, parent.project_id)
        if not project:
            parent.status = "failed"
            parent.error_message = "Project not found"
            db.commit()
            return
        script = db.scalars(
            select(Script).where(Script.project_id == project.id, Script.is_active.is_(True)).order_by(Script.version.desc())
        ).first()
        if not script:
            parent.status = "failed"
            parent.error_message = "Generate a script before starting automatic production."
            parent.status_message = parent.error_message
            db.commit()
            return
        scene_ids = [s.id for s in db.scalars(select(Scene).where(Scene.script_id == script.id).order_by(Scene.scene_index)).all()]

    try:
        if not scene_ids:
            raise RuntimeError("The active script has no scenes.")

        # Prepare scene images first so each scene has an approved image anchor.
        for idx, scene_id in enumerate(scene_ids):
            if not _set_parent_progress(parent_job_id, 5 + int(idx / max(1, len(scene_ids)) * 35), f"Generating scene images ({idx + 1}/{len(scene_ids)})"):
                return
            with SessionLocal() as db:
                scene = db.get(Scene, scene_id)
                project = db.get(Project, scene.project_id)
                image_asset = get_active_scene_image_asset(db, scene.id)
                media = db.get(MediaAsset, image_asset.media_asset_id) if image_asset else None
                needs_image = image_asset is None or not aspect_ratio_matches(media.width if media else None, media.height if media else None, project.aspect_ratio)
                if image_asset and scene.approval_status != "approved":
                    approve_scene_image(db, scene)
                    db.commit()
            if needs_image:
                child_id = _new_child_job(project, scene, "scene_image", project.image_model)
                _run_child(child_id, run_scene_image_job)
                with SessionLocal() as db:
                    scene = db.get(Scene, scene_id)
                    project = db.get(Project, scene.project_id)
                    image_asset = get_active_scene_image_asset(db, scene.id)
                    media = db.get(MediaAsset, image_asset.media_asset_id) if image_asset else None
                    if project.aspect_ratio != "1:1" and not aspect_ratio_matches(media.width if media else None, media.height if media else None, project.aspect_ratio):
                        raise RuntimeError(f"Scene {scene.scene_index} image aspect does not match project ratio {project.aspect_ratio}; selected image model={project.image_model}.")
                    approve_scene_image(db, scene)
                    db.commit()

        # Generate clips in order. Each completed clip produces an ending-frame
        # continuity image consumed by the next clip's video-generation job.
        for idx, scene_id in enumerate(scene_ids):
            if not _set_parent_progress(parent_job_id, 42 + int(idx / max(1, len(scene_ids)) * 42), f"Generating scene videos ({idx + 1}/{len(scene_ids)})"):
                return
            with SessionLocal() as db:
                scene = db.get(Scene, scene_id)
                project = db.get(Project, scene.project_id)
                video_asset = get_active_scene_video_asset(db, scene.id)
                media = db.get(MediaAsset, video_asset.media_asset_id) if video_asset else None
                needs_video = video_asset is None or not aspect_ratio_matches(media.width if media else None, media.height if media else None, project.aspect_ratio)
            if needs_video:
                child_id = _new_child_job(project, scene, "scene_video", project.video_model)
                _run_child(child_id, run_scene_video_job)
                with SessionLocal() as db:
                    scene = db.get(Scene, scene_id)
                    project = db.get(Project, scene.project_id)
                    video_asset = get_active_scene_video_asset(db, scene.id)
                    media = db.get(MediaAsset, video_asset.media_asset_id) if video_asset else None
                    if project.aspect_ratio != "1:1" and not aspect_ratio_matches(media.width if media else None, media.height if media else None, project.aspect_ratio):
                        raise RuntimeError(f"Scene {scene.scene_index} video aspect does not match project ratio {project.aspect_ratio}; selected video model={project.video_model}.")

        if not _set_parent_progress(parent_job_id, 88, "Composing the finished MP4 with FFmpeg"):
            return
        with SessionLocal() as db:
            project = db.get(Project, project.id)
            script = db.get(Script, script.id)
            version = (db.scalar(select(Render.version).where(Render.project_id == project.id).order_by(Render.version.desc()).limit(1)) or 0) + 1
            render = Render(
                owner_id=project.owner_id,
                project_id=project.id,
                version=version,
                status="queued",
                render_config_json={**(project.timeline_config_json or {}), "script_id": script.id},
            )
            db.add(render)
            db.commit()
            db.refresh(render)
            render_job_id = _new_child_job(project, None, "project_render", "ffmpeg", render)
        _run_child(render_job_id, run_project_render_job)

        with SessionLocal() as db:
            parent = db.get(GenerationJob, parent_job_id)
            if parent and parent.status != "cancelled":
                parent.status = "completed"
                parent.progress_percent = 100
                parent.status_message = "Automatic production and FFmpeg render completed"
                parent.completed_at = datetime.now(timezone.utc)
                db.commit()
                publish_job_event(parent_job_id, "completed", {"status": "completed", "progress_percent": 100, "status_message": parent.status_message})
    except Exception as exc:
        logger.exception("Automatic production failed for parent job %s", parent_job_id)
        with SessionLocal() as db:
            parent = db.get(GenerationJob, parent_job_id)
            if parent and parent.status != "cancelled":
                parent.status = "failed"
                parent.error_code = "AUTO_PRODUCTION_FAILED"
                parent.error_message = str(exc)
                parent.status_message = str(exc)[:480]
                parent.completed_at = datetime.now(timezone.utc)
                db.commit()
                publish_job_event(parent_job_id, "failed", {"status": "failed", "error_code": parent.error_code, "error_message": parent.error_message, "status_message": parent.status_message})
