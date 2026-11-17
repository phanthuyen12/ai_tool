import uuid

import pytest

from app.core.database import SessionLocal
from app.models.entities import (
    GenerationJob,
    MediaAsset,
    Project,
    Scene,
    SceneAsset,
    Script,
    User,
)
from app.services.generation.continuity import (
    ContinuityGateError,
    activate_scene_asset_version,
    approve_scene_image,
    validate_master_gate_for_scene,
)
from app.workers.tasks import claim_job_atomically


def test_job_claiming_idempotency_and_continuity_gate():
    db = SessionLocal()
    tag = uuid.uuid4().hex[:8]
    user = User(
        email=f"test_{tag}@local.test",
        full_name="Test Runner",
        password_hash="scrypt$test$test",
    )
    db.add(user)
    db.flush()

    try:
        project = Project(
            owner_id=user.id,
            name=f"Test Proj {tag}",
            topic="XAUUSD",
            auto_approve_master=False,
        )
        db.add(project)
        db.flush()

        script = Script(
            project_id=project.id,
            version=1,
            is_active=True,
            title="Test Script",
        )
        db.add(script)
        db.flush()

        s1 = Scene(
            project_id=project.id,
            script_id=script.id,
            scene_index=1,
            title="Master",
            is_master_scene=True,
            approval_status="awaiting_approval",
            status="image_ready",
        )
        s2 = Scene(
            project_id=project.id,
            script_id=script.id,
            scene_index=2,
            title="Scene 2",
            is_master_scene=False,
            approval_status="pending",
            status="draft",
        )
        db.add_all([s1, s2])
        db.flush()

        ma1 = MediaAsset(
            owner_id=user.id,
            project_id=project.id,
            storage_backend="local",
            storage_key=f"image/test_{tag}_1.png",
            original_filename="m1.png",
            mime_type="image/png",
            asset_kind="image",
        )
        ma2 = MediaAsset(
            owner_id=user.id,
            project_id=project.id,
            storage_backend="local",
            storage_key=f"image/test_{tag}_2.png",
            original_filename="m2.png",
            mime_type="image/png",
            asset_kind="image",
        )
        db.add_all([ma1, ma2])
        db.flush()

        sa1 = SceneAsset(
            scene_id=s1.id,
            media_asset_id=ma1.id,
            asset_role="master_image",
            version=1,
            is_active=False,
        )
        sa2 = SceneAsset(
            scene_id=s1.id,
            media_asset_id=ma2.id,
            asset_role="master_image",
            version=2,
            is_active=True,
        )
        db.add_all([sa1, sa2])
        db.flush()

        # Scene 2 must be blocked until Master Scene 1 is approved
        with pytest.raises(ContinuityGateError):
            validate_master_gate_for_scene(db, project, s2)

        # Put a pre-existing image on Scene 2, then approve Scene 1 -> Scene 2 gets flagged needs_review
        db.add(
            SceneAsset(
                scene_id=s2.id,
                media_asset_id=ma1.id,
                asset_role="continuity_image",
                version=1,
                is_active=True,
            )
        )
        db.flush()

        flagged = approve_scene_image(db, s1)
        assert s2.id in flagged
        assert s2.continuity_status == "needs_review"
        assert s1.approval_status == "approved"

        # Now Scene 2 passes the Master Gate
        master_asset = validate_master_gate_for_scene(db, project, s2)
        assert master_asset is not None
        assert master_asset.id == sa2.id

        # Rollback Scene 1 asset back to v1
        activated = activate_scene_asset_version(db, s1.id, sa1.id)
        db.refresh(sa2)
        assert activated.id == sa1.id
        assert sa1.is_active is True
        assert sa2.is_active is False

        # Test worker atomic job claiming and restart idempotency
        job = GenerationJob(
            owner_id=user.id,
            project_id=project.id,
            scene_id=s1.id,
            job_type="scene_image",
            provider_model="gemini-3.1-flash-image-portrait-2k",
            status="queued",
            idempotency_key=f"idem-{tag}",
        )
        db.add(job)
        db.commit()

        claimed = claim_job_atomically(db, job.id, worker_id="test-worker-1")
        assert claimed is not None
        assert claimed.status == "running"

        # Second claim on active running job must return None
        second_claim = claim_job_atomically(db, job.id, worker_id="test-worker-2")
        assert second_claim is None

        # Mark completed -> simulating worker restart cannot re-claim completed jobs
        claimed.status = "completed"
        db.commit()
        after_complete = claim_job_atomically(db, job.id, worker_id="test-worker-3")
        assert after_complete is None
    finally:
        db.delete(user)
        db.commit()
        db.close()

