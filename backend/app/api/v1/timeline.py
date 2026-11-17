from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.entities import Project, Scene, Script, User
from app.schemas.studio import TimelineUpdateRequest
from app.services.media_storage.manager import MediaStorageService
from app.services.serializers import serialize_scene

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("/{project_id}")
def get_timeline(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    active_script = db.scalars(
        select(Script)
        .where(Script.project_id == project.id, Script.is_active.is_(True))
        .order_by(Script.version.desc())
    ).first()
    scenes: list[Scene] = []
    if active_script:
        scenes = db.scalars(
            select(Scene).where(Scene.script_id == active_script.id).order_by(Scene.scene_index.asc())
        ).all()

    storage = MediaStorageService.from_db(db, owner_id=current_user.id)
    serialized = [serialize_scene(db, s, storage) for s in scenes]
    total_dur = round(sum((s.actual_duration_sec or s.planned_duration_sec) for s in scenes), 2)

    return {
        "project_id": project.id,
        "script_id": active_script.id if active_script else None,
        "aspect_ratio": project.aspect_ratio,
        "voice_mode": project.voice_mode,
        "total_duration_sec": total_dur,
        "timeline_config_json": project.timeline_config_json or {},
        "scenes": serialized,
    }


@router.put("/{project_id}")
def update_timeline(
    project_id: str,
    payload: TimelineUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = db.get(Project, project_id)
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Project not found")

    if payload.timeline_config_json:
        merged_cfg = dict(project.timeline_config_json or {})
        merged_cfg.update(payload.timeline_config_json)
        project.timeline_config_json = merged_cfg

    cursor = 0.0
    for idx, item in enumerate(payload.scenes, start=1):
        sc = db.get(Scene, item.scene_id)
        if not sc or sc.project_id != project.id:
            continue
        sc.scene_index = idx
        sc.is_master_scene = idx == 1
        if item.trim_start_sec is not None:
            sc.trim_start_sec = max(0.0, item.trim_start_sec)
        if item.trim_end_sec is not None:
            sc.trim_end_sec = item.trim_end_sec if item.trim_end_sec > 0 else None
        if item.transition_type is not None:
            sc.transition_type = item.transition_type
        if item.character_id is not None:
            sc.character_id = item.character_id
        if item.background_id is not None:
            sc.background_id = item.background_id

        base_dur = sc.actual_duration_sec or sc.planned_duration_sec
        eff_dur = (
            min(base_dur, sc.trim_end_sec - sc.trim_start_sec)
            if (sc.trim_end_sec and sc.trim_end_sec > sc.trim_start_sec)
            else max(0.5, base_dur - sc.trim_start_sec)
        )
        sc.start_time_sec = round(cursor, 2)
        sc.end_time_sec = round(cursor + eff_dur, 2)
        cursor += eff_dur

    db.commit()
    return get_timeline(project_id=project.id, current_user=current_user, db=db)

