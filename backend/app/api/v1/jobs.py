import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.events import subscribe_job_events
from app.core.security import get_current_user
from app.models.entities import GenerationAttempt, GenerationJob, User
from app.schemas.studio import GenerationAttemptResponse, GenerationJobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _serialize_job(db: Session, job: GenerationJob, include_attempts: bool = False) -> GenerationJobResponse:
    attempts_out: list[GenerationAttemptResponse] = []
    if include_attempts:
        attempts = db.scalars(
            select(GenerationAttempt)
            .where(GenerationAttempt.job_id == job.id)
            .order_by(GenerationAttempt.attempt_number.asc())
        ).all()
        attempts_out = [
            GenerationAttemptResponse(
                id=a.id,
                attempt_number=a.attempt_number,
                model_used=a.model_used,
                prompt_sent=a.prompt_sent,
                reference_summary_json=a.reference_summary_json or [],
                http_status=a.http_status,
                upstream_error_code=a.upstream_error_code,
                error_message=a.error_message,
                response_excerpt=a.response_excerpt,
                duration_ms=a.duration_ms,
                status=a.status,
                created_at=a.created_at,
            )
            for a in attempts
        ]
    return GenerationJobResponse(
        id=job.id,
        project_id=job.project_id,
        batch_id=job.batch_id,
        scene_id=job.scene_id,
        render_id=job.render_id,
        job_type=job.job_type,
        provider_model=job.provider_model,
        status=job.status,
        progress_percent=job.progress_percent,
        status_message=job.status_message,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        error_code=job.error_code,
        error_message=job.error_message,
        result_json=job.result_json or {},
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        attempts=attempts_out,
    )


@router.get("", response_model=list[GenerationJobResponse])
def list_jobs(
    project_id: Optional[str] = Query(default=None),
    batch_id: Optional[str] = Query(default=None),
    scene_id: Optional[str] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(GenerationJob).where(GenerationJob.owner_id == current_user.id)
    if project_id:
        stmt = stmt.where(GenerationJob.project_id == project_id)
    if batch_id:
        stmt = stmt.where(GenerationJob.batch_id == batch_id)
    if scene_id:
        stmt = stmt.where(GenerationJob.scene_id == scene_id)
    if status_filter:
        stmt = stmt.where(GenerationJob.status == status_filter)
    rows = db.scalars(stmt.order_by(GenerationJob.created_at.desc()).limit(100)).all()
    return [_serialize_job(db, j, include_attempts=bool(scene_id)) for j in rows]


@router.get("/{job_id}", response_model=GenerationJobResponse)
def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(GenerationJob, job_id)
    if not job or job.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(db, job, include_attempts=True)


@router.post("/{job_id}/cancel", response_model=GenerationJobResponse)
def cancel_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(GenerationJob, job_id)
    if not job or job.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("completed", "failed", "cancelled"):
        return _serialize_job(db, job, include_attempts=True)
    job.status = "cancelled"
    job.status_message = "Cancelled by user"
    db.commit()
    db.refresh(job)
    return _serialize_job(db, job, include_attempts=True)


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.get(GenerationJob, job_id)
    if not job or job.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")

    initial = {
        "event": "snapshot",
        "job_id": job.id,
        "status": job.status,
        "progress_percent": job.progress_percent,
        "status_message": job.status_message,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "result": job.result_json or {},
    }

    async def _stream():
        yield f"event: snapshot\ndata: {json.dumps(initial)}\n\n"
        if job.status in ("completed", "failed", "cancelled"):
            return
        async for chunk in subscribe_job_events(job_id):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

