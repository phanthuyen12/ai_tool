import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import GenerationJob, Project, Script, Scene, User
from app.schemas.studio import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.serializers import serialize_project
from app.services.provider_config import get_effective_runtime_config
from app.workers.project_pipeline import run_project_auto_production

router = APIRouter(prefix="/projects", tags=["projects"])


def _model_for_aspect(model: str, aspect_ratio: str) -> str:
    """Keep portrait/landscape model variants aligned with the project's canvas."""
    if aspect_ratio == "16:9" and "portrait" in model:
        return model.replace("portrait", "landscape")
    if aspect_ratio == "9:16" and "landscape" in model:
        return model.replace("landscape", "portrait")
    if aspect_ratio == "1:1" and ("portrait" in model or "landscape" in model):
        return model.replace("portrait", "square").replace("landscape", "square")
    return model


@router.post("/{project_id}/auto-produce", status_code=status.HTTP_202_ACCEPTED)
def start_project_auto_production(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    active_script = db.scalars(
        select(Script).where(Script.project_id == project.id, Script.is_active.is_(True)).order_by(Script.version.desc())
    ).first()
    if not active_script or not db.scalars(select(Scene).where(Scene.script_id == active_script.id)).first():
        raise HTTPException(status_code=409, detail="Generate a script with scenes before starting automatic production.")
    existing = db.scalars(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.job_type == "project_auto_production",
            GenerationJob.status.in_(["queued", "running"]),
        ).order_by(GenerationJob.created_at.desc())
    ).first()
    if existing:
        return {"id": existing.id, "status": existing.status, "status_message": existing.status_message}

    job = GenerationJob(
        owner_id=current_user.id,
        project_id=project.id,
        job_type="project_auto_production",
        provider_model="automatic-pipeline",
        status="queued",
        progress_percent=0,
        status_message="Queued automatic scene generation and FFmpeg render",
        max_attempts=1,
        idempotency_key=f"auto-production:{project.id}:{uuid.uuid4().hex[:10]}",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    run_project_auto_production.send(job.id)
    return {"id": job.id, "status": job.status, "status_message": job.status_message}


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    search: Optional[str] = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Project).where(Project.owner_id == current_user.id)
    if status_filter:
        stmt = stmt.where(Project.status == status_filter)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where((Project.name.ilike(like)) | (Project.topic.ilike(like)))
    rows = db.scalars(stmt.order_by(Project.updated_at.desc())).all()
    return [serialize_project(db, p) for p in rows]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    language = (payload.language or runtime.default_language or "vi-VN").strip()
    voice_mode = payload.voice_mode or runtime.default_voice_mode or "ai_generated"
    script_mode = payload.script_mode or "ai_presenter"
    voice_description = (payload.voice_description or runtime.default_voice_description).strip()
    project = Project(
        owner_id=current_user.id,
        name=payload.name.strip(),
        topic_category=payload.topic_category.strip(),
        topic=payload.topic.strip(),
        goal_description=payload.goal_description.strip(),
        language=language,
        target_duration_seconds=payload.target_duration_seconds,
        aspect_ratio=payload.aspect_ratio,
        export_quality=payload.export_quality,
        visual_style=payload.visual_style.strip(),
        default_character_id=payload.default_character_id,
        default_background_id=payload.default_background_id,
        image_model=_model_for_aspect(payload.image_model.strip(), payload.aspect_ratio),
        video_model=_model_for_aspect(payload.video_model.strip(), payload.aspect_ratio),
        voice_mode=voice_mode,
        script_mode=script_mode,
        timeline_config_json={"voice_description": voice_description},
        video_count=payload.video_count,
        global_rules=payload.global_rules.strip(),
        character_schedule_json=[s.model_dump() for s in payload.character_schedule_json],
        auto_approve_master=payload.auto_approve_master,
        status="draft",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return serialize_project(db, project)


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return serialize_project(db, project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    data = payload.model_dump(exclude_unset=True)
    voice_description = data.pop("voice_description", None)
    if voice_description is not None:
        timeline = dict(project.timeline_config_json or {})
        timeline["voice_description"] = voice_description.strip()
        project.timeline_config_json = timeline
    if data.get("language") is None:
        data.pop("language", None)
    if data.get("voice_mode") is None:
        data.pop("voice_mode", None)
    if data.get("script_mode") is None:
        data.pop("script_mode", None)
    if data.get("aspect_ratio"):
        target_aspect = data["aspect_ratio"]
        data["image_model"] = _model_for_aspect(data.get("image_model", project.image_model), target_aspect)
        data["video_model"] = _model_for_aspect(data.get("video_model", project.video_model), target_aspect)
    if "character_schedule_json" in data and payload.character_schedule_json is not None:
        data["character_schedule_json"] = [s.model_dump() for s in payload.character_schedule_json]
    for k, v in data.items():
        setattr(project, k, v)
    db.commit()
    db.refresh(project)
    return serialize_project(db, project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return None
