import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import GenerationJob, Project, Render, Script, User
from app.schemas.studio import RenderCreateRequest, RenderResponse
from app.services.media_storage.manager import MediaStorageService
from app.services.serializers import serialize_render
from app.workers.tasks import run_project_render_job

router = APIRouter(prefix="/renders", tags=["renders"])


@router.get("", response_model=list[RenderResponse])
def list_renders(
    project_id: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    stmt = select(Render).where(Render.owner_id == current_user.id)
    if project_id:
        stmt = stmt.where(Render.project_id == project_id)
    rows = db.scalars(stmt.order_by(Render.created_at.desc()).limit(100)).all()
    return [serialize_render(db, r, storage) for r in rows]


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_render(
    payload: RenderCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, payload.project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    script_id = payload.script_id
    if not script_id:
        active_script = db.scalars(
            select(Script)
            .where(Script.project_id == project.id, Script.is_active.is_(True))
            .order_by(Script.version.desc())
        ).first()
        script_id = active_script.id if active_script else None

    prev_renders = db.scalars(select(Render).where(Render.project_id == project.id)).all()
    next_ver = max((r.version for r in prev_renders), default=0) + 1

    cfg = dict(project.timeline_config_json or {})
    if payload.render_config_override:
        cfg.update(payload.render_config_override)
    if script_id:
        cfg["script_id"] = script_id

    render = Render(
        owner_id=current_user.id,
        project_id=project.id,
        version=next_ver,
        status="queued",
        render_config_json=cfg,
    )
    db.add(render)
    db.flush()

    job = GenerationJob(
        owner_id=current_user.id,
        project_id=project.id,
        render_id=render.id,
        job_type="project_render",
        provider_model="ffmpeg",
        status="queued",
        progress_percent=0,
        status_message="Queued FFmpeg video composition",
        max_attempts=1,
        idempotency_key=f"render:{render.id}:{uuid.uuid4().hex[:8]}",
    )
    db.add(job)
    db.commit()
    db.refresh(render)
    db.refresh(job)

    run_project_render_job.send(job.id)
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return {
        "render": serialize_render(db, render, storage),
        "job_id": job.id,
    }


@router.get("/{render_id}", response_model=RenderResponse)
def get_render(
    render_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    render = db.get(Render, render_id)
    if not render or render.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Render not found")
    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    return serialize_render(db, render, storage)

