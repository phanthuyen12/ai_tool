import re
import uuid
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import select

from app.cli import cli
from app.core.database import SessionLocal
from app.models.entities import (
    Background,
    Character,
    MediaAsset,
    Project,
    Render,
    Scene,
    Script,
)
from app.services.media_storage.manager import MediaStorageService


def _cleanup_project_tree(project_id: str) -> None:
    db = SessionLocal()
    try:
        storage = MediaStorageService.from_db(db)
        proj = db.get(Project, project_id)
        char_id = proj.default_character_id if proj else None
        bg_id = proj.default_background_id if proj else None

        assets = db.scalars(select(MediaAsset).where(MediaAsset.project_id == project_id)).all()
        renders = db.scalars(select(Render).where(Render.project_id == project_id)).all()
        extra_asset_ids = {
            aid
            for r in renders
            for aid in (r.output_media_asset_id, r.subtitle_media_asset_id, r.thumbnail_media_asset_id)
            if aid
        }
        if char_id:
            ch = db.get(Character, char_id)
            if ch and ch.primary_image_asset_id:
                extra_asset_ids.add(ch.primary_image_asset_id)
        if bg_id:
            bg = db.get(Background, bg_id)
            if bg and bg.media_asset_id:
                extra_asset_ids.add(bg.media_asset_id)

        for aid in extra_asset_ids:
            ma = db.get(MediaAsset, aid)
            if ma and ma not in assets:
                assets.append(ma)

        if proj:
            db.delete(proj)
            db.flush()

        if char_id:
            ch = db.get(Character, char_id)
            if ch and ch.name.startswith("Expert Demo "):
                db.delete(ch)
        if bg_id:
            bg = db.get(Background, bg_id)
            if bg and bg.name.startswith("Office Demo "):
                db.delete(bg)
        db.flush()

        for ma in assets:
            if ma.storage_backend == "local":
                local_file = storage.local.resolve_path(ma.storage_key)
                if local_file.exists():
                    local_file.unlink()
            db.delete(ma)

        db.commit()
    finally:
        db.close()


def test_cli_doctor_and_models():
    runner = CliRunner()
    res_doc = runner.invoke(cli, ["doctor"])
    assert res_doc.exit_code == 0, res_doc.output
    assert "PostgreSQL   : OK" in res_doc.output
    assert "FFmpeg" in res_doc.output

    res_mod = runner.invoke(cli, ["models"])
    assert res_mod.exit_code == 0, res_mod.output
    assert "gemini-3.1-flash-image-portrait-2k" in res_mod.output
    assert "omni-1.2-flash-8s-portrait" in res_mod.output


def test_cli_project_script_prompts_and_master_gate():
    runner = CliRunner()
    tag = uuid.uuid4().hex[:6]
    proj_id = None
    try:
        res_create = runner.invoke(
            cli,
            [
                "project",
                "create",
                "--name",
                f"CLI Pytest {tag}",
                "--topic",
                "XAUUSD Liquidity Sweep",
                "--duration",
                "16",
            ],
        )
        assert res_create.exit_code == 0, res_create.output
        m = re.search(r"Project created:\s+([0-9a-fA-F-]{36})", res_create.output)
        assert m, res_create.output
        proj_id = m.group(1)

        res_script = runner.invoke(cli, ["script", "generate", proj_id])
        assert res_script.exit_code == 0, res_script.output
        assert "#01" in res_script.output
        assert "#02" in res_script.output

        db = SessionLocal()
        try:
            active_script = db.scalars(
                select(Script).where(Script.project_id == proj_id, Script.is_active.is_(True))
            ).first()
            assert active_script is not None
            scenes = db.scalars(
                select(Scene).where(Scene.script_id == active_script.id).order_by(Scene.scene_index.asc())
            ).all()
            assert len(scenes) == 2
            s1_id, s2_id = scenes[0].id, scenes[1].id
        finally:
            db.close()

        res_prompts = runner.invoke(cli, ["scene", "prompts", s1_id])
        assert res_prompts.exit_code == 0, res_prompts.output
        assert "IMAGE PROMPT" in res_prompts.output
        assert "VIDEO PROMPT" in res_prompts.output

        # Continuity Master Gate: Scene 02 image generation must be blocked before Scene 01 is approved
        res_gate = runner.invoke(cli, ["scene", "gen-image", s2_id])
        assert res_gate.exit_code != 0
        assert "Master" in res_gate.output or "approve" in res_gate.output.lower()

        # Cannot approve Scene 01 before its image exists
        res_approve = runner.invoke(cli, ["scene", "approve", s1_id])
        assert res_approve.exit_code != 0
        assert "Cannot approve scene before an image is generated" in res_approve.output
    finally:
        if proj_id:
            _cleanup_project_tree(proj_id)


def test_cli_demo_local_end_to_end(tmp_path: Path):
    runner = CliRunner()
    out_mp4 = tmp_path / "cli_demo_out.mp4"
    proj_id = None
    try:
        res = runner.invoke(cli, ["demo-local", "--output", str(out_mp4)])
        assert res.exit_code == 0, res.output
        m = re.search(r"Project created:\s+([0-9a-fA-F-]{36})", res.output)
        if m:
            proj_id = m.group(1)
        assert "Render completed" in res.output
        assert out_mp4.exists()
        assert out_mp4.stat().st_size > 10_000
    finally:
        if proj_id:
            _cleanup_project_tree(proj_id)
