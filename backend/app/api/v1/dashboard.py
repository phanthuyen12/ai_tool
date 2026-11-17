from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import Batch, GenerationJob, Project, Render, User
from app.services.media_storage.base import get_disk_status

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    total_projects = (
        db.scalar(select(func.count(Project.id)).where(Project.owner_id == current_user.id)) or 0
    )
    generating_videos = (
        db.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.owner_id == current_user.id,
                GenerationJob.job_type.in_(["scene_video", "project_render"]),
                GenerationJob.status.in_(["queued", "running"]),
            )
        )
        or 0
    )
    completed_videos = (
        db.scalar(
            select(func.count(Render.id)).where(
                Render.owner_id == current_user.id, Render.status == "completed"
            )
        )
        or 0
    )
    failed_jobs = (
        db.scalar(
            select(func.count(GenerationJob.id)).where(
                GenerationJob.owner_id == current_user.id, GenerationJob.status == "failed"
            )
        )
        or 0
    )
    total_rendered_duration = (
        db.scalar(
            select(func.coalesce(func.sum(Render.duration_seconds), 0.0)).where(
                Render.owner_id == current_user.id, Render.status == "completed"
            )
        )
        or 0.0
    )

    recent_batches = db.scalars(
        select(Batch)
        .where(Batch.owner_id == current_user.id)
        .order_by(Batch.created_at.desc())
        .limit(5)
    ).all()
    recent_jobs = db.scalars(
        select(GenerationJob)
        .where(GenerationJob.owner_id == current_user.id)
        .order_by(GenerationJob.created_at.desc())
        .limit(10)
    ).all()

    return {
        "metrics": {
            "total_projects": int(total_projects),
            "generating_videos": int(generating_videos),
            "completed_videos": int(completed_videos),
            "failed_jobs": int(failed_jobs),
            "total_rendered_duration_seconds": round(float(total_rendered_duration), 1),
        },
        "disk": get_disk_status(),
        "recent_batches": [
            {
                "id": b.id,
                "name": b.name,
                "project_id": b.project_id,
                "total_videos": b.total_videos,
                "completed_videos": b.completed_videos,
                "failed_videos": b.failed_videos,
                "status": b.status,
                "created_at": b.created_at,
            }
            for b in recent_batches
        ],
        "recent_jobs": [
            {
                "id": j.id,
                "project_id": j.project_id,
                "scene_id": j.scene_id,
                "job_type": j.job_type,
                "provider_model": j.provider_model,
                "status": j.status,
                "progress_percent": j.progress_percent,
                "attempt_count": j.attempt_count,
                "error_code": j.error_code,
                "error_message": j.error_message,
                "created_at": j.created_at,
            }
            for j in recent_jobs
        ],
    }

