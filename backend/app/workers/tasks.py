import contextlib
import logging
import socket
import time
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import dramatiq
import redis
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.events import publish_job_event
from app.models.entities import (
    Background,
    Batch,
    BatchItem,
    Character,
    CharacterRule,
    GenerationAttempt,
    GenerationJob,
    MediaAsset,
    Project,
    Render,
    Scene,
    SceneAsset,
    ScenePrompt,
)
from app.services.flow2api.client import Flow2APIClient
from app.services.flow2api.parser import Flow2APIUpstreamError, classify_flow2api_error
from app.services.generation.continuity import (
    ContinuityGateError,
    assemble_video_continuity_references,
    activate_scene_asset_version,
    assemble_continuity_references,
    evaluate_image_continuity,
    get_active_scene_image_asset,
    get_active_scene_video_asset,
)
from app.services.media_storage.base import TempWorkspaceManager, cleanup_stale_temp_dirs, get_disk_status
from app.services.media_storage.manager import MediaStorageService
from app.services.prompt_builder.builder import (
    adjust_video_prompt_for_audio_filter_retry,
    build_image_prompt_layers,
    build_video_prompt_layers,
)
from app.services.provider_config import get_effective_runtime_config
from app.services.video_editor.composer import TimelineClipInput, aspect_ratio_matches, compose_timeline_video
from app.workers.broker import redis_broker  # noqa: F401  Ensure broker configured

logger = logging.getLogger(__name__)
WORKER_NODE_ID = f"{socket.gethostname()}"


def claim_job_atomically(db: Session, job_id: str, worker_id: str = WORKER_NODE_ID) -> Optional[GenerationJob]:
    """
    Spec XI & XVII #15: Atomically claim a queued job (or stale interrupted job after restart).
    Already completed/cancelled jobs are NEVER re-executed.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=20)
    stmt = (
        update(GenerationJob)
        .where(
            GenerationJob.id == job_id,
            (GenerationJob.status == "queued")
            | ((GenerationJob.status == "running") & (GenerationJob.heartbeat_at < stale_cutoff)),
        )
        .values(
            status="running",
            worker_id=worker_id,
            started_at=now,
            heartbeat_at=now,
            progress_percent=5,
            status_message="Worker claimed job",
        )
        .returning(GenerationJob.id)
    )
    claimed_id = db.execute(stmt).scalar_one_or_none()
    db.commit()
    if not claimed_id:
        return None
    return db.get(GenerationJob, claimed_id)


@contextlib.contextmanager
def distributed_concurrency_slot(
    kind: str, max_slots: int, delay_seconds: int
) -> Iterator[None]:
    """Redis slot limiter preventing concurrent overload against upstream Flow2API."""
    settings = get_settings()
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    acquired_key: Optional[str] = None
    ttl = 1000 if kind == "video" else 240
    deadline = time.time() + 1800
    try:
        while time.time() < deadline:
            for slot in range(max(1, max_slots)):
                candidate = f"flow_slot:{kind}:{slot}"
                if r.set(candidate, WORKER_NODE_ID, nx=True, ex=ttl):
                    acquired_key = candidate
                    break
            if acquired_key:
                break
            time.sleep(1.5)
        yield
        if delay_seconds > 0:
            time.sleep(delay_seconds)
    finally:
        if acquired_key:
            try:
                r.delete(acquired_key)
            except Exception:
                pass
        r.close()


def _recalculate_script_timings(db: Session, script_id: str) -> None:
    scenes = db.scalars(
        select(Scene).where(Scene.script_id == script_id).order_by(Scene.scene_index.asc())
    ).all()
    cursor = 0.0
    for sc in scenes:
        dur = sc.actual_duration_sec or sc.planned_duration_sec
        if sc.trim_end_sec and sc.trim_end_sec > sc.trim_start_sec:
            dur = min(dur, sc.trim_end_sec - sc.trim_start_sec)
        sc.start_time_sec = round(cursor, 2)
        sc.end_time_sec = round(cursor + dur, 2)
        cursor += dur


def _resolve_scene_prompt_text(
    db: Session, project: Project, scene: Scene, prompt_type: str
) -> str:
    active = db.scalars(
        select(ScenePrompt).where(
            ScenePrompt.scene_id == scene.id,
            ScenePrompt.prompt_type == prompt_type,
            ScenePrompt.is_active.is_(True),
        )
    ).first()
    if active and active.compiled_prompt.strip():
        prompt = active.compiled_prompt
        if prompt_type == "video" and "veo" in (project.video_model or "").lower() and "r2v" in (project.video_model or "").lower() and "Veo R2V reference hierarchy" not in prompt:
            prompt += (
                "\n\nVeo R2V reference hierarchy: image 1 is this scene's exact opening composition. "
                "Use the remaining images only to preserve the same presenter's face, age, clothing, and the same set. "
                "Do not merge, collage, or switch between reference scenes; keep one continuous shot and the same camera side. "
                "Speak only this scene's quoted dialogue exactly once. Do not replay, repeat, recap, or add any previous-scene dialogue."
            )
        return prompt

    char = db.get(Character, scene.character_id or project.default_character_id) if (
        scene.character_id or project.default_character_id
    ) else None
    rule = (
        db.scalars(select(CharacterRule).where(CharacterRule.character_id == char.id)).first()
        if char
        else None
    )
    bg = db.get(Background, scene.background_id or project.default_background_id) if (
        scene.background_id or project.default_background_id
    ) else None

    if prompt_type == "image":
        layers, compiled = build_image_prompt_layers(
            scene=scene,
            project=project,
            character=char,
            char_rule=rule,
            background=bg,
            is_master_scene=scene.is_master_scene or scene.scene_index == 1,
        )
    else:
        layers, compiled = build_video_prompt_layers(
            scene=scene,
            project=project,
            character=char,
            char_rule=rule,
            background=bg,
        )

    prompt_row = ScenePrompt(
        scene_id=scene.id,
        prompt_type=prompt_type,
        version=1,
        is_active=True,
        layers_json=layers,
        locked_layers_json=[],
        compiled_prompt=compiled,
    )
    db.add(prompt_row)
    db.flush()
    return compiled


def _fail_job(
    db: Session,
    job: GenerationJob,
    scene: Optional[Scene],
    error_code: str,
    message: str,
    scene_fail_status: str = "failed",
) -> None:
    now = datetime.now(timezone.utc)
    job.status = "failed"
    job.error_code = error_code
    job.error_message = message
    job.status_message = message[:480]
    job.completed_at = now
    if scene:
        scene.status = scene_fail_status
    db.commit()
    publish_job_event(
        job.id,
        "failed",
        {
            "status": "failed",
            "progress_percent": job.progress_percent,
            "error_code": error_code,
            "error_message": message,
            "status_message": job.status_message,
        },
    )


def _sync_batch_progress(db: Session, batch_item_id: Optional[str], ok: bool, error_msg: Optional[str] = None) -> None:
    if not batch_item_id:
        return
    item = db.get(BatchItem, batch_item_id)
    if not item:
        return
    batch = db.get(Batch, item.batch_id)
    if not batch:
        return
    if ok:
        item.status = "completed"
        item.error_message = None
    else:
        item.status = "failed"
        item.error_message = error_msg
    items = db.scalars(select(BatchItem).where(BatchItem.batch_id == batch.id)).all()
    batch.completed_videos = sum(1 for x in items if x.status == "completed")
    batch.failed_videos = sum(1 for x in items if x.status == "failed")
    if batch.completed_videos + batch.failed_videos >= batch.total_videos:
        batch.status = "completed" if batch.failed_videos == 0 else (
            "partially_failed" if batch.completed_videos > 0 else "failed"
        )
    db.flush()


@dramatiq.actor(max_retries=0, time_limit=300_000)
def run_scene_image_job(job_id: str) -> None:
    """Execute Flow2API image generation with Continuity Engine and persistent storage download."""
    cleanup_stale_temp_dirs()
    db = SessionLocal()
    try:
        job = claim_job_atomically(db, job_id)
        if not job:
            return
        scene = db.get(Scene, job.scene_id) if job.scene_id else None
        project = db.get(Project, job.project_id)
        if not scene or not project:
            _fail_job(db, job, scene, "NOT_FOUND", "Project or Scene no longer exists")
            return

        scene.status = "image_generating"
        project.status = "image_generating"
        db.commit()

        runtime = get_effective_runtime_config(db, owner_id=project.owner_id)
        storage = MediaStorageService(runtime)

        try:
            references, ref_summary = assemble_continuity_references(
                db, project=project, scene=scene, storage=storage
            )
        except ContinuityGateError as exc:
            _fail_job(db, job, scene, "CONTINUITY_GATE", str(exc), scene_fail_status="draft")
            return

        prompt_text = _resolve_scene_prompt_text(db, project, scene, "image")
        model = job.provider_model or project.image_model
        client = Flow2APIClient(
            base_url=runtime.flow2api_base_url,
            api_key=runtime.flow2api_api_key,
            image_timeout=runtime.flow2api_image_timeout,
            video_timeout=runtime.flow2api_video_timeout,
        )

        publish_job_event(
            job.id,
            "running",
            {"status": "running", "progress_percent": 15, "status_message": f"Calling {model}..."},
        )

        last_err: Optional[Flow2APIUpstreamError] = None
        result_url: Optional[str] = None
        raw_content = ""

        with distributed_concurrency_slot(
            "image", runtime.worker_image_concurrency, runtime.worker_request_delay_seconds
        ):
            max_loops = max(1, job.max_attempts)
            for attempt in range(1, max_loops + 1):
                # Refresh cancellation check
                db.refresh(job)
                if job.status == "cancelled":
                    return
                job.attempt_count = attempt
                job.heartbeat_at = datetime.now(timezone.utc)
                db.commit()

                started_ms = int(time.monotonic() * 1000)
                try:
                    def _on_prog(msg: str, pct: int) -> None:
                        publish_job_event(
                            job.id,
                            "progress",
                            {"status": "running", "progress_percent": pct, "status_message": msg},
                        )

                    res = client.generate_image(
                        model=model,
                        prompt=prompt_text,
                        references=references,
                        stream=True,
                        on_progress=_on_prog,
                    )
                    result_url = res["url"]
                    raw_content = res.get("raw_content", "")
                    duration_ms = int(time.monotonic() * 1000) - started_ms
                    db.add(
                        GenerationAttempt(
                            job_id=job.id,
                            attempt_number=attempt,
                            model_used=model,
                            prompt_sent=prompt_text,
                            reference_summary_json=ref_summary,
                            http_status=200,
                            response_excerpt=raw_content[:500],
                            duration_ms=duration_ms,
                            status="succeeded",
                        )
                    )
                    db.commit()
                    break
                except Flow2APIUpstreamError as exc:
                    last_err = exc
                    duration_ms = int(time.monotonic() * 1000) - started_ms
                    decision = classify_flow2api_error(
                        status_code=exc.status_code, message=str(exc)
                    )
                    can_retry = decision.retryable and attempt <= decision.max_retries and attempt < max_loops
                    db.add(
                        GenerationAttempt(
                            job_id=job.id,
                            attempt_number=attempt,
                            model_used=model,
                            prompt_sent=prompt_text,
                            reference_summary_json=ref_summary,
                            http_status=exc.status_code,
                            upstream_error_code=decision.error_code,
                            error_message=decision.user_message,
                            response_excerpt=exc.response_excerpt[:500],
                            duration_ms=duration_ms,
                            status="retried" if can_retry else "failed",
                        )
                    )
                    db.commit()
                    if not can_retry:
                        _fail_job(db, job, scene, decision.error_code, decision.user_message)
                        return
                    wait_sec = decision.backoff_seconds * attempt
                    job.status_message = f"Retrying after {wait_sec}s ({decision.error_code})"
                    db.commit()
                    publish_job_event(
                        job.id,
                        "retrying",
                        {"status": "running", "progress_percent": 20, "status_message": job.status_message},
                    )
                    time.sleep(wait_sec)

        if not result_url:
            msg = str(last_err) if last_err else "Image generation produced no URL"
            _fail_job(db, job, scene, "NO_OUTPUT", msg)
            return

        # Immediately download from temporary Flow2API /tmp URL into persistent storage
        publish_job_event(
            job.id,
            "progress",
            {"status": "running", "progress_percent": 92, "status_message": "Saving generated image to persistent storage"},
        )
        with TempWorkspaceManager(prefix="img") as tmp_dir:
            ext = "jpg" if ".jpg" in result_url.lower() or ".jpeg" in result_url.lower() else "png"
            tmp_file = tmp_dir / f"scene_{scene.scene_index:02d}.{ext}"
            client.download_temporary_asset(result_url, tmp_file, timeout=120.0)
            mime = "image/jpeg" if ext == "jpg" else "image/png"
            media_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=tmp_file,
                original_filename=f"{project.id}_scene_{scene.scene_index:02d}.{ext}",
                mime_type=mime,
                asset_kind="image",
                project_id=project.id,
                extra_metadata={"flow2api_source_url": result_url, "references": ref_summary},
            )

        role = "master_image" if (scene.is_master_scene or scene.scene_index == 1) else "continuity_image"
        prev_versions = db.scalars(
            select(SceneAsset).where(SceneAsset.scene_id == scene.id, SceneAsset.asset_role == role)
        ).all()
        next_ver = max((v.version for v in prev_versions), default=0) + 1
        for prev in prev_versions:
            prev.is_active = False

        scene_asset = SceneAsset(
            scene_id=scene.id,
            media_asset_id=media_asset.id,
            asset_role=role,
            version=next_ver,
            is_active=True,
            generation_job_id=job.id,
            continuity_metadata_json={"references_used": ref_summary},
        )
        db.add(scene_asset)
        db.flush()

        c_status, c_notes = evaluate_image_continuity(media_asset, project)
        scene.continuity_status = c_status
        scene.continuity_notes = c_notes
        if project.auto_approve_master or not (scene.is_master_scene or scene.scene_index == 1):
            scene.approval_status = "approved" if project.auto_approve_master else "awaiting_approval"
        else:
            scene.approval_status = "awaiting_approval"
        scene.status = "approved" if scene.approval_status == "approved" else "image_ready"

        job.status = "completed"
        job.progress_percent = 100
        job.status_message = "Image generated and stored"
        job.completed_at = datetime.now(timezone.utc)
        job.result_json = {
            "media_asset_id": media_asset.id,
            "scene_asset_id": scene_asset.id,
            "asset_url": storage.resolve_access_url(media_asset),
            "width": media_asset.width,
            "height": media_asset.height,
        }
        db.commit()
        publish_job_event(
            job.id,
            "completed",
            {
                "status": "completed",
                "progress_percent": 100,
                "status_message": job.status_message,
                "result": job.result_json,
            },
        )
    except Exception as exc:
        logger.exception("Unhandled error in run_scene_image_job %s", job_id)
        try:
            job = db.get(GenerationJob, job_id)
            scene = db.get(Scene, job.scene_id) if job and job.scene_id else None
            if job:
                _fail_job(db, job, scene, "WORKER_EXCEPTION", str(exc))
        except Exception:
            pass
    finally:
        db.close()


@dramatiq.actor(max_retries=0, time_limit=1_200_000)
def run_scene_video_job(job_id: str) -> None:
    """Execute Flow2API Omni/Veo video generation with audio-filter recovery & ffprobe duration sync."""
    cleanup_stale_temp_dirs()
    db = SessionLocal()
    try:
        job = claim_job_atomically(db, job_id)
        if not job:
            return
        scene = db.get(Scene, job.scene_id) if job.scene_id else None
        project = db.get(Project, job.project_id)
        if not scene or not project:
            _fail_job(db, job, scene, "NOT_FOUND", "Project or Scene no longer exists")
            return

        active_img = get_active_scene_image_asset(db, scene.id)
        if not active_img:
            _fail_job(
                db,
                job,
                scene,
                "MISSING_SCENE_IMAGE",
                "Generate and approve a reference image for this scene before generating video.",
                scene_fail_status="draft",
            )
            return
        if scene.approval_status != "approved" and not project.auto_approve_master:
            _fail_job(
                db,
                job,
                scene,
                "SCENE_NOT_APPROVED",
                "Approve the scene image before generating its video clip.",
                scene_fail_status="image_ready",
            )
            return

        scene.status = "video_generating"
        project.status = "video_generating"
        db.commit()

        runtime = get_effective_runtime_config(db, owner_id=project.owner_id)
        storage = MediaStorageService(runtime)
        img_media = db.get(MediaAsset, active_img.media_asset_id)
        if not img_media:
            _fail_job(db, job, scene, "MISSING_MEDIA", "Scene reference media asset missing")
            return

        model = job.provider_model or project.video_model
        if "veo" in model.lower() and "r2v" in model.lower():
            continuity_refs = assemble_video_continuity_references(
                db, project=project, scene=scene, current_scene_image=active_img
            )
            ref_payload = [storage.prepare_reference_for_flow2api(media) for media, _ in continuity_refs]
            ref_summary = [f"{label}:{media.id}" for media, label in continuity_refs]
        else:
            ref_payload = [storage.prepare_reference_for_flow2api(img_media)]
            ref_summary = [f"scene_image:{img_media.id}"]
        # Start the next clip from the actual last frame of the previous clip.
        previous_scene = db.scalars(
            select(Scene).where(
                Scene.script_id == scene.script_id,
                Scene.scene_index < scene.scene_index,
            ).order_by(Scene.scene_index.desc())
        ).first()
        if previous_scene:
            previous_frame = db.scalars(
                select(SceneAsset).where(
                    SceneAsset.scene_id == previous_scene.id,
                    SceneAsset.asset_role == "continuity_frame",
                    SceneAsset.is_active.is_(True),
                ).order_by(SceneAsset.version.desc())
            ).first()
            previous_frame_media = db.get(MediaAsset, previous_frame.media_asset_id) if previous_frame else None
            if previous_frame_media:
                ref_payload.insert(0, storage.prepare_reference_for_flow2api(previous_frame_media))
                ref_summary.insert(0, f"previous_scene_last_frame:{previous_frame_media.id}")
        base_prompt = _resolve_scene_prompt_text(db, project, scene, "video")
        base_prompt = (
            f"{base_prompt.rstrip()}\n\n"
            "STRICT VISUAL LOCK: Reproduce only the people, objects, set, and visual elements already present in the supplied opening reference. "
            "Do not invent, add, remove, replace, decorate, or transform anything: no extra props, products, charts, screens, signs, text, logos, furniture, "
            "people, animals, graphics, particles, or effects. Keep the same presenter, face, clothing, exact room layout, object positions, colors, exposure, "
            "lighting, framing, and camera angle for the entire clip. One uninterrupted shot; no cuts, transitions, zooms, pans, or reframing. "
            "Animate only the explicitly requested action and the exact dialogue for this scene. If an action would require adding a new object or element, "
            "omit that action and preserve the reference image."
        )
        keep_dialogue = (
            "[DEEP_WORK_MODE]" not in (project.global_rules or "")
            and project.voice_mode == "ai_generated"
            and bool((scene.dialogue or "").strip())
        )

        client = Flow2APIClient(
            base_url=runtime.flow2api_base_url,
            api_key=runtime.flow2api_api_key,
            image_timeout=runtime.flow2api_image_timeout,
            video_timeout=runtime.flow2api_video_timeout,
        )

        publish_job_event(
            job.id,
            "running",
            {"status": "running", "progress_percent": 10, "status_message": f"Starting video generation ({model})..."},
        )

        video_url: Optional[str] = None
        flow_media_id: Optional[str] = None
        last_err: Optional[Flow2APIUpstreamError] = None
        current_prompt = base_prompt

        with distributed_concurrency_slot(
            "video", runtime.worker_video_concurrency, runtime.worker_request_delay_seconds
        ):
            max_loops = max(1, job.max_attempts)
            for attempt in range(1, max_loops + 1):
                db.refresh(job)
                if job.status == "cancelled":
                    return
                job.attempt_count = attempt
                job.heartbeat_at = datetime.now(timezone.utc)
                db.commit()

                started_ms = int(time.monotonic() * 1000)
                try:
                    def _on_prog(msg: str, pct: int) -> None:
                        publish_job_event(
                            job.id,
                            "progress",
                            {"status": "running", "progress_percent": pct, "status_message": msg},
                        )

                    res = client.generate_video(
                        model=model,
                        prompt=current_prompt,
                        references=ref_payload,
                        stream=True,
                        on_progress=_on_prog,
                    )
                    video_url = res["url"]
                    flow_media_id = res.get("media_id")
                    duration_ms = int(time.monotonic() * 1000) - started_ms
                    db.add(
                        GenerationAttempt(
                            job_id=job.id,
                            attempt_number=attempt,
                            model_used=model,
                            prompt_sent=current_prompt,
                            reference_summary_json=ref_summary,
                            http_status=200,
                            response_excerpt=(res.get("raw_content") or "")[:500],
                            duration_ms=duration_ms,
                            status="succeeded",
                        )
                    )
                    db.commit()
                    break
                except Flow2APIUpstreamError as exc:
                    last_err = exc
                    duration_ms = int(time.monotonic() * 1000) - started_ms
                    decision = classify_flow2api_error(
                        status_code=exc.status_code, message=str(exc)
                    )
                    can_retry = decision.retryable and attempt <= decision.max_retries and attempt < max_loops
                    db.add(
                        GenerationAttempt(
                            job_id=job.id,
                            attempt_number=attempt,
                            model_used=model,
                            prompt_sent=current_prompt,
                            reference_summary_json=ref_summary,
                            http_status=exc.status_code,
                            upstream_error_code=decision.error_code,
                            error_message=decision.user_message,
                            response_excerpt=exc.response_excerpt[:500],
                            duration_ms=duration_ms,
                            status="retried" if can_retry else "failed",
                        )
                    )
                    db.commit()
                    if not can_retry:
                        _fail_job(db, job, scene, decision.error_code, decision.user_message)
                        return

                    if decision.adjust_audio_prompt:
                        current_prompt = adjust_video_prompt_for_audio_filter_retry(
                            base_prompt, attempt + 1, keep_dialogue=keep_dialogue
                        )

                    wait_sec = decision.backoff_seconds * attempt
                    job.status_message = f"Retrying after {wait_sec}s ({decision.error_code})"
                    db.commit()
                    publish_job_event(
                        job.id,
                        "retrying",
                        {"status": "running", "progress_percent": 18, "status_message": job.status_message},
                    )
                    time.sleep(wait_sec)

        if not video_url:
            msg = str(last_err) if last_err else "Video generation produced no URL"
            _fail_job(db, job, scene, "NO_OUTPUT", msg)
            return

        publish_job_event(
            job.id,
            "progress",
            {"status": "running", "progress_percent": 92, "status_message": "Downloading MP4 & probing duration"},
        )
        with TempWorkspaceManager(prefix="vid") as tmp_dir:
            tmp_file = tmp_dir / f"scene_{scene.scene_index:02d}.mp4"
            client.download_temporary_asset(video_url, tmp_file, timeout=300.0)
            last_frame_file = tmp_dir / f"scene_{scene.scene_index:02d}_last_frame.jpg"
            subprocess.run(
                ["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(tmp_file), "-frames:v", "1", "-q:v", "2", str(last_frame_file)],
                check=True, capture_output=True, timeout=60,
            )
            media_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=tmp_file,
                original_filename=f"{project.id}_scene_{scene.scene_index:02d}.mp4",
                mime_type="video/mp4",
                asset_kind="video",
                project_id=project.id,
                extra_metadata={"flow2api_source_url": video_url, "flow_media_id": flow_media_id},
            )
            last_frame_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=last_frame_file,
                original_filename=f"{project.id}_scene_{scene.scene_index:02d}_last_frame.jpg",
                mime_type="image/jpeg",
                asset_kind="image",
                project_id=project.id,
                extra_metadata={"derived_from_scene_video": scene.id, "frame_position": "last"},
            )

        prev_videos = db.scalars(
            select(SceneAsset).where(
                SceneAsset.scene_id == scene.id, SceneAsset.asset_role == "scene_video"
            )
        ).all()
        next_ver = max((v.version for v in prev_videos), default=0) + 1
        for prev in prev_videos:
            prev.is_active = False

        scene_asset = SceneAsset(
            scene_id=scene.id,
            media_asset_id=media_asset.id,
            asset_role="scene_video",
            version=next_ver,
            is_active=True,
            flow_media_id=flow_media_id,
            generation_job_id=job.id,
            continuity_metadata_json={"references_used": ref_summary},
        )
        db.add(scene_asset)
        prior_frames = db.scalars(select(SceneAsset).where(
            SceneAsset.scene_id == scene.id, SceneAsset.asset_role == "continuity_frame"
        )).all()
        frame_version = max((frame.version for frame in prior_frames), default=0) + 1
        for frame in prior_frames:
            frame.is_active = False
        db.add(SceneAsset(
            scene_id=scene.id,
            media_asset_id=last_frame_asset.id,
            asset_role="continuity_frame",
            version=frame_version,
            is_active=True,
            generation_job_id=job.id,
            continuity_metadata_json={"derived_from_video": media_asset.id, "position": "last_frame"},
        ))

        # Spec IX: Never assume clips are always exactly 8s; update actual duration from ffprobe!
        if media_asset.duration_seconds:
            scene.actual_duration_sec = media_asset.duration_seconds
            _recalculate_script_timings(db, scene.script_id)

        scene.status = "video_ready"
        job.status = "completed"
        job.progress_percent = 100
        job.status_message = "Video clip generated and stored"
        job.completed_at = datetime.now(timezone.utc)
        job.result_json = {
            "media_asset_id": media_asset.id,
            "scene_asset_id": scene_asset.id,
            "asset_url": storage.resolve_access_url(media_asset),
            "actual_duration_sec": media_asset.duration_seconds,
            "flow_media_id": flow_media_id,
        }
        db.commit()
        publish_job_event(
            job.id,
            "completed",
            {
                "status": "completed",
                "progress_percent": 100,
                "status_message": job.status_message,
                "result": job.result_json,
            },
        )
    except Exception as exc:
        logger.exception("Unhandled error in run_scene_video_job %s", job_id)
        try:
            job = db.get(GenerationJob, job_id)
            scene = db.get(Scene, job.scene_id) if job and job.scene_id else None
            if job:
                _fail_job(db, job, scene, "WORKER_EXCEPTION", str(exc))
        except Exception:
            pass
    finally:
        db.close()


@dramatiq.actor(max_retries=0, time_limit=1_200_000)
def run_project_render_job(job_id: str) -> None:
    """Execute FFmpeg Video Composer render job using TempWorkspaceManager and persistent storage."""
    cleanup_stale_temp_dirs()
    db = SessionLocal()
    try:
        job = claim_job_atomically(db, job_id)
        if not job:
            return
        render = db.get(Render, job.render_id) if job.render_id else None
        project = db.get(Project, job.project_id)
        if not render or not project:
            _fail_job(db, job, None, "NOT_FOUND", "Render or Project not found")
            return

        disk = get_disk_status()
        if not disk["healthy"]:
            msg = f"Insufficient disk space ({disk['free_gb']} GB free, require >= {disk['min_required_gb']} GB)"
            render.status = "failed"
            render.error_message = msg
            _fail_job(db, job, None, "LOW_DISK_SPACE", msg)
            return

        render.status = "rendering"
        project.status = "rendering"
        db.commit()

        runtime = get_effective_runtime_config(db, owner_id=project.owner_id)
        storage = MediaStorageService(runtime)
        cfg = render.render_config_json or project.timeline_config_json or {}
        script_id = cfg.get("script_id")

        scene_stmt = select(Scene).where(Scene.project_id == project.id)
        if script_id:
            scene_stmt = scene_stmt.where(Scene.script_id == script_id)
        scenes = db.scalars(scene_stmt.order_by(Scene.scene_index.asc())).all()

        mismatched_scenes: list[str] = []
        for sc in scenes:
            rel = get_active_scene_video_asset(db, sc.id)
            media = db.get(MediaAsset, rel.media_asset_id) if rel else None
            if media and not aspect_ratio_matches(media.width, media.height, project.aspect_ratio):
                mismatched_scenes.append(f"Scene {sc.scene_index}: {media.width}x{media.height}")
        if mismatched_scenes:
            msg = (
                f"Clip aspect does not match project canvas ({project.aspect_ratio}): "
                + ", ".join(mismatched_scenes[:5])
                + ". Select matching landscape/portrait models and regenerate those clips before rendering."
            )
            render.status = "failed"
            render.error_message = msg
            db.commit()
            _fail_job(db, job, None, "ASPECT_MISMATCH", msg)
            return

        publish_job_event(
            job.id,
            "progress",
            {"status": "running", "progress_percent": 20, "status_message": "Collecting scene clips for FFmpeg"},
        )

        with TempWorkspaceManager(prefix="render") as tmp_dir:
            clip_inputs: list[TimelineClipInput] = []
            for sc in scenes:
                v_asset_rel = get_active_scene_video_asset(db, sc.id)
                if not v_asset_rel:
                    continue
                v_media = db.get(MediaAsset, v_asset_rel.media_asset_id)
                if not v_media:
                    continue
                local_clip = tmp_dir / f"src_{sc.scene_index:03d}.mp4"
                storage.copy_asset_to_path(v_media, local_clip)

                ext_audio_path: Optional[Path] = None
                tts_rel = db.scalars(
                    select(SceneAsset).where(
                        SceneAsset.scene_id == sc.id,
                        SceneAsset.asset_role == "external_tts_audio",
                        SceneAsset.is_active.is_(True),
                    )
                ).first()
                if project.voice_mode == "external_tts" and tts_rel:
                    tts_media = db.get(MediaAsset, tts_rel.media_asset_id)
                    if tts_media:
                        ext_audio_path = tmp_dir / f"tts_{sc.scene_index:03d}.audio"
                        storage.copy_asset_to_path(tts_media, ext_audio_path)

                clip_inputs.append(
                    TimelineClipInput(
                        scene_index=sc.scene_index,
                        video_path=local_clip,
                        caption_text=sc.caption_text or sc.dialogue,
                        trim_start_sec=sc.trim_start_sec,
                        trim_end_sec=sc.trim_end_sec,
                        external_audio_path=ext_audio_path,
                        transition_type=sc.transition_type,
                    )
                )

            if not clip_inputs:
                msg = "No scene video clips are ready to compose yet."
                render.status = "failed"
                render.error_message = msg
                _fail_job(db, job, None, "NO_CLIPS", msg)
                return

            def _optional_asset_file(asset_id: Optional[str], name: str) -> Optional[Path]:
                if not asset_id:
                    return None
                ma = db.get(MediaAsset, asset_id)
                if not ma:
                    return None
                dest = tmp_dir / name
                return storage.copy_asset_to_path(ma, dest)

            bgm_path = _optional_asset_file(cfg.get("bgm_asset_id"), "bgm_track.audio")
            wm_path = _optional_asset_file(cfg.get("watermark_asset_id"), "watermark.png")
            intro_path = _optional_asset_file(cfg.get("intro_asset_id"), "intro.mp4")
            outro_path = _optional_asset_file(cfg.get("outro_asset_id"), "outro.mp4")

            out_mp4 = tmp_dir / f"final_render_v{render.version}.mp4"
            out_srt = tmp_dir / f"captions_v{render.version}.srt"
            out_thumb = tmp_dir / f"thumb_v{render.version}.jpg"

            publish_job_event(
                job.id,
                "progress",
                {"status": "running", "progress_percent": 50, "status_message": "Compositing clips with FFmpeg..."},
            )

            stats = compose_timeline_video(
                clips=clip_inputs,
                workspace_dir=tmp_dir,
                output_mp4_path=out_mp4,
                output_srt_path=out_srt,
                output_thumb_path=out_thumb,
                aspect_ratio=project.aspect_ratio,
                export_quality=project.export_quality,
                voice_muted=bool(cfg.get("voice_muted", False)),
                voice_volume=float(cfg.get("voice_volume", 1.0)),
                bgm_path=bgm_path,
                bgm_muted=bool(cfg.get("bgm_muted", False)),
                bgm_volume=float(cfg.get("bgm_volume", 0.15)),
                watermark_path=wm_path,
                burn_subtitles=bool(cfg.get("burn_subtitles", False)),
                default_transition=str(cfg.get("default_transition", "none")),
                transition_duration_sec=float(cfg.get("transition_duration_sec", 0.4)),
                intro_path=intro_path,
                outro_path=outro_path,
                apply_color_grade=bool(cfg.get("color_grade", True)),
            )

            publish_job_event(
                job.id,
                "progress",
                {"status": "running", "progress_percent": 88, "status_message": "Uploading rendered MP4 to storage"},
            )

            mp4_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=out_mp4,
                original_filename=f"{project.name}_v{render.version}.mp4",
                mime_type="video/mp4",
                asset_kind="video",
                project_id=project.id,
                extra_metadata={"render_id": render.id},
            )
            srt_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=out_srt,
                original_filename=f"{project.name}_v{render.version}.srt",
                mime_type="application/x-subrip",
                asset_kind="subtitle",
                project_id=project.id,
                extra_metadata={"render_id": render.id},
            )
            thumb_asset = storage.save_file(
                db,
                owner_id=project.owner_id,
                file_path=out_thumb,
                original_filename=f"{project.name}_v{render.version}_thumb.jpg",
                mime_type="image/jpeg",
                asset_kind="image",
                project_id=project.id,
                extra_metadata={"render_id": render.id},
            )

        render.status = "completed"
        render.output_media_asset_id = mp4_asset.id
        render.subtitle_media_asset_id = srt_asset.id
        render.thumbnail_media_asset_id = thumb_asset.id
        render.duration_seconds = float(stats["duration_seconds"])
        render.file_size_bytes = int(stats["file_size_bytes"])
        render.resolution = str(stats["resolution"])
        render.ffmpeg_log_excerpt = stats.get("ffmpeg_log_excerpt")

        project.status = "completed"
        _sync_batch_progress(db, render.batch_item_id, ok=True)

        job.status = "completed"
        job.progress_percent = 100
        job.status_message = "Render completed"
        job.completed_at = datetime.now(timezone.utc)
        job.result_json = {
            "render_id": render.id,
            "mp4_url": storage.resolve_access_url(mp4_asset),
            "srt_url": storage.resolve_access_url(srt_asset),
            "thumbnail_url": storage.resolve_access_url(thumb_asset),
            "duration_seconds": render.duration_seconds,
            "file_size_bytes": render.file_size_bytes,
            "resolution": render.resolution,
        }
        db.commit()
        publish_job_event(
            job.id,
            "completed",
            {
                "status": "completed",
                "progress_percent": 100,
                "status_message": "Render completed",
                "result": job.result_json,
            },
        )
    except Exception as exc:
        logger.exception("Unhandled error in run_project_render_job %s", job_id)
        try:
            job = db.get(GenerationJob, job_id)
            render = db.get(Render, job.render_id) if job and job.render_id else None
            if render:
                render.status = "failed"
                render.error_message = str(exc)
                _sync_batch_progress(db, render.batch_item_id, ok=False, error_msg=str(exc))
            if job:
                _fail_job(db, job, None, "RENDER_FAILED", str(exc))
        except Exception:
            pass
    finally:
        db.close()
