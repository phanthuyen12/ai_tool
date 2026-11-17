import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import (
    Batch,
    BatchItem,
    Character,
    GenerationJob,
    Project,
    Scene,
    Script,
    User,
)
from app.schemas.studio import BatchCreateRequest, BatchItemResponse, BatchResponse
from app.services.ai_script.llm_generator import compute_scene_speech_metrics, generate_script_draft
from app.services.ai_script.scene_planner import parse_model_clip_duration, plan_scene_windows
from app.services.provider_config import get_effective_runtime_config
from app.api.v1.scripts import create_scene_prompts_for_scene
from app.workers.tasks import run_scene_image_job

router = APIRouter(prefix="/batches", tags=["batches"])


def _serialize_batch(db: Session, batch: Batch) -> BatchResponse:
    proj = db.get(Project, batch.project_id)
    items = db.scalars(
        select(BatchItem)
        .where(BatchItem.batch_id == batch.id)
        .order_by(BatchItem.item_index.asc())
    ).all()
    return BatchResponse(
        id=batch.id,
        project_id=batch.project_id,
        project_name=proj.name if proj else None,
        name=batch.name,
        total_videos=batch.total_videos,
        completed_videos=batch.completed_videos,
        failed_videos=batch.failed_videos,
        concurrency_limit=batch.concurrency_limit,
        request_delay_seconds=batch.request_delay_seconds,
        status=batch.status,
        error_summary=batch.error_summary,
        items=[
            BatchItemResponse(
                id=i.id,
                item_index=i.item_index,
                title=i.title,
                topic_variant=i.topic_variant,
                script_id=i.script_id,
                render_id=i.render_id,
                status=i.status,
                error_message=i.error_message,
            )
            for i in items
        ],
        created_at=batch.created_at,
    )


@router.get("", response_model=list[BatchResponse])
def list_batches(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(Batch).where(Batch.owner_id == current_user.id).order_by(Batch.created_at.desc())
    ).all()
    return [_serialize_batch(db, b) for b in rows]


@router.post("", response_model=BatchResponse, status_code=status.HTTP_201_CREATED)
def create_batch(
    payload: BatchCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    batch = Batch(
        owner_id=current_user.id,
        project_id=project.id,
        name=payload.name.strip(),
        total_videos=payload.total_videos,
        concurrency_limit=payload.concurrency_limit,
        request_delay_seconds=payload.request_delay_seconds,
        status="draft",
    )
    db.add(batch)
    db.flush()

    for idx in range(1, payload.total_videos + 1):
        variant = (
            payload.topic_variants[idx - 1].strip()
            if idx - 1 < len(payload.topic_variants) and payload.topic_variants[idx - 1].strip()
            else f"{project.topic} — Part {idx:02d}"
        )
        db.add(
            BatchItem(
                batch_id=batch.id,
                project_id=project.id,
                item_index=idx,
                title=f"{project.name} #{idx:02d}",
                topic_variant=variant,
                status="queued",
            )
        )

    db.commit()
    db.refresh(batch)
    return _serialize_batch(db, batch)


@router.get("/{batch_id}", response_model=BatchResponse)
def get_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(Batch, batch_id)
    if not batch or batch.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    return _serialize_batch(db, batch)


@router.post("/{batch_id}/start", response_model=BatchResponse)
def start_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(Batch, batch_id)
    if not batch or batch.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    project = db.get(Project, batch.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    chars = db.scalars(select(Character).where(Character.owner_id == current_user.id)).all()
    char_names = {c.id: c.name for c in chars}
    clip_dur = parse_model_clip_duration(project.video_model, default=8)
    items = db.scalars(
        select(BatchItem).where(BatchItem.batch_id == batch.id).order_by(BatchItem.item_index.asc())
    ).all()

    queued_jobs: list[str] = []
    for item in items:
        if item.status == "completed":
            continue
        if not item.script_id:
            windows = plan_scene_windows(
                target_duration_sec=project.target_duration_seconds,
                clip_duration_sec=clip_dur,
                character_schedule=project.character_schedule_json,
                default_character_id=project.default_character_id,
                default_background_id=project.default_background_id,
            )
            draft = generate_script_draft(
                runtime_cfg=runtime,
                topic=item.topic_variant or project.topic,
                topic_category=project.topic_category,
                goal_description=project.goal_description,
                language=project.language,
                visual_style=project.visual_style,
                windows=windows,
                character_names=char_names,
            )
            script = Script(
                project_id=project.id,
                batch_item_id=item.id,
                version=item.item_index,
                is_active=(item.item_index == 1),
                title=draft.title.strip()[:300],
                objective=draft.objective,
                opening_hook=draft.opening_hook,
                narration_summary=draft.narration_summary,
                total_estimated_seconds=float(sum(w.duration_sec for w in windows)),
                llm_provider_used=draft.llm_provider_used,
                raw_payload_json=draft.raw_payload,
            )
            db.add(script)
            db.flush()
            item.script_id = script.id

            for win, sc_draft in zip(windows, draft.scenes):
                est, warn = compute_scene_speech_metrics(
                    sc_draft.dialogue, win.duration_sec, project.language
                )
                scene = Scene(
                    project_id=project.id,
                    script_id=script.id,
                    scene_index=win.scene_index,
                    title=sc_draft.title,
                    start_time_sec=win.start_time_sec,
                    end_time_sec=win.end_time_sec,
                    planned_duration_sec=win.duration_sec,
                    character_id=win.character_id,
                    background_id=win.background_id,
                    dialogue=sc_draft.dialogue,
                    caption_text=sc_draft.caption_text,
                    estimated_speech_sec=est,
                    speech_warning=warn,
                    character_action=sc_draft.character_action,
                    camera_motion=sc_draft.camera_motion,
                    transition_type=sc_draft.transition_type,
                    is_master_scene=win.is_master_scene,
                )
                db.add(scene)
                db.flush()
                create_scene_prompts_for_scene(db, project=project, scene=scene)

        # Queue Master Image (Scene 01) for each item
        first_scene = db.scalars(
            select(Scene)
            .where(Scene.script_id == item.script_id)
            .order_by(Scene.scene_index.asc())
        ).first()
        if first_scene and first_scene.status == "draft":
            job = GenerationJob(
                owner_id=current_user.id,
                project_id=project.id,
                batch_id=batch.id,
                batch_item_id=item.id,
                scene_id=first_scene.id,
                job_type="scene_image",
                provider_model=project.image_model,
                status="queued",
                status_message=f"Batch {batch.name} item #{item.item_index} master image queued",
                idempotency_key=f"batch:{batch.id}:item:{item.id}:master:{uuid.uuid4().hex[:6]}",
            )
            first_scene.status = "image_queued"
            item.status = "running"
            db.add(job)
            db.flush()
            queued_jobs.append(job.id)

    batch.status = "running"
    db.commit()

    for jid in queued_jobs:
        run_scene_image_job.send(jid)

    db.refresh(batch)
    return _serialize_batch(db, batch)


@router.post("/{batch_id}/pause", response_model=BatchResponse)
def pause_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(Batch, batch_id)
    if not batch or batch.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch.status = "paused"
    queued = db.scalars(
        select(GenerationJob).where(
            GenerationJob.batch_id == batch.id, GenerationJob.status == "queued"
        )
    ).all()
    for j in queued:
        j.status = "cancelled"
        j.status_message = "Paused/cancelled by batch pause"
    db.commit()
    db.refresh(batch)
    return _serialize_batch(db, batch)


@router.post("/{batch_id}/resume", response_model=BatchResponse)
def resume_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return start_batch(batch_id=batch_id, current_user=current_user, db=db)


@router.post("/{batch_id}/cancel", response_model=BatchResponse)
def cancel_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    batch = db.get(Batch, batch_id)
    if not batch or batch.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch.status = "cancelled"
    queued = db.scalars(
        select(GenerationJob).where(
            GenerationJob.batch_id == batch.id, GenerationJob.status == "queued"
        )
    ).all()
    for j in queued:
        j.status = "cancelled"
        j.status_message = "Cancelled with batch"
    db.commit()
    db.refresh(batch)
    return _serialize_batch(db, batch)
