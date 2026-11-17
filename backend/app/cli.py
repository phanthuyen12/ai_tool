import io
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import click
import httpx
import redis
import uvicorn
from PIL import Image, ImageDraw
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.v1.scripts import create_scene_prompts_for_scene
from app.core.config import get_settings, normalize_v1_base_url
from app.core.database import SessionLocal
from app.core.events import job_channel
from app.core.logging import configure_logging
from app.core.security import (
    encrypt_secret,
    mask_secret,
    validate_external_url,
    validate_media_bytes,
)
from app.main import _bootstrap_defaults
from app.models.entities import (
    Background,
    Character,
    CharacterReference,
    CharacterRule,
    GenerationAttempt,
    GenerationJob,
    MediaAsset,
    Project,
    ProviderSetting,
    Render,
    Scene,
    SceneAsset,
    ScenePrompt,
    Script,
    User,
)
from app.services.ai_script.llm_generator import (
    compute_scene_speech_metrics,
    generate_script_draft,
)
from app.services.ai_script.scene_planner import (
    parse_model_clip_duration,
    plan_scene_windows,
    trim_dialogue_to_fit,
)
from app.services.flow2api.client import Flow2APIClient
from app.services.flow2api.parser import DEFAULT_FLOW2API_CATALOG, Flow2APIUpstreamError
from app.services.generation.continuity import (
    ContinuityGateError,
    activate_scene_asset_version,
    approve_scene_image,
    get_active_scene_image_asset,
    validate_master_gate_for_scene,
)
from app.services.media_storage.base import TempWorkspaceManager, get_disk_status
from app.services.media_storage.manager import MediaStorageService
from app.services.prompt_builder.builder import (
    PROMPT_LAYER_KEYS,
    build_image_prompt_layers,
    build_video_prompt_layers,
)
from app.services.provider_config import get_effective_runtime_config
from app.services.vision_caption.captioner import generate_image_caption
from app.workers.tasks import (
    run_project_render_job,
    run_scene_image_job,
    run_scene_video_job,
)


def _get_db_and_owner() -> tuple[Session, User]:
    _bootstrap_defaults()
    db = SessionLocal()
    owner = db.scalars(select(User).order_by(User.created_at.asc())).first()
    if not owner:
        db.close()
        raise click.ClickException("No studio owner user found in database.")
    return db, owner


def _resolve_by_id_or_name(rows: list, ref: str, label: str):
    cleaned = ref.strip()
    for r in rows:
        if r.id == cleaned:
            return r
    prefix_matches = [r for r in rows if r.id.startswith(cleaned)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise click.ClickException(f"Ambiguous {label} ID prefix '{ref}' ({len(prefix_matches)} matches).")
    name_matches = [r for r in rows if getattr(r, "name", "").lower() == cleaned.lower()]
    if len(name_matches) == 1:
        return name_matches[0]
    raise click.ClickException(f"{label} '{ref}' not found.")


def _resolve_project(db: Session, owner_id: str, ref: str) -> Project:
    rows = db.scalars(select(Project).where(Project.owner_id == owner_id)).all()
    return _resolve_by_id_or_name(rows, ref, "Project")


def _resolve_scene(db: Session, owner_id: str, ref: str) -> tuple[Scene, Project]:
    projects = db.scalars(select(Project).where(Project.owner_id == owner_id)).all()
    proj_ids = {p.id: p for p in projects}
    scenes = db.scalars(select(Scene).where(Scene.project_id.in_(proj_ids.keys()))).all() if proj_ids else []
    scene = _resolve_by_id_or_name(scenes, ref, "Scene")
    return scene, proj_ids[scene.project_id]


def _ingest_media_source(
    db: Session,
    *,
    owner_id: str,
    storage: MediaStorageService,
    source: str,
    asset_kind: str = "image",
    project_id: Optional[str] = None,
) -> MediaAsset:
    path = Path(source).expanduser()
    if path.exists() and path.is_file():
        data = path.read_bytes()
        filename = path.name
    elif source.startswith(("http://", "https://")):
        safe_url = validate_external_url(source)
        with httpx.Client(timeout=60.0, follow_redirects=False) as client:
            resp = client.get(safe_url)
            resp.raise_for_status()
            data = resp.content
        filename = Path(urlparse(safe_url).path).name or f"imported.{asset_kind}"
    else:
        raise click.ClickException(f"File or URL not found: {source}")

    mime = validate_media_bytes(data, asset_kind)
    return storage.save_bytes(
        db,
        owner_id=owner_id,
        data=data,
        original_filename=filename,
        mime_type=mime,
        asset_kind=asset_kind,
        project_id=project_id,
    )


def _asset_location_hint(storage: MediaStorageService, asset: Optional[MediaAsset]) -> str:
    if not asset:
        return "-"
    if asset.storage_backend == "local":
        return str(storage.local.resolve_path(asset.storage_key))
    return asset.public_url or asset.storage_key


def _execute_job(db: Session, job: GenerationJob, actor, enqueue: bool) -> GenerationJob:
    if enqueue:
        actor.send(job.id)
        click.echo(f"Enqueued job {job.id} ({job.job_type}) -> status: queued")
        return job

    settings = get_settings()
    stop_evt = threading.Event()

    def _listen_progress() -> None:
        try:
            r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
            ps = r.pubsub()
            ps.subscribe(job_channel(job.id))
            last_msg = ""
            while not stop_evt.is_set():
                msg = ps.get_message(ignore_subscribe_messages=True, timeout=0.4)
                if msg and msg.get("data"):
                    try:
                        payload = json.loads(msg["data"])
                        pct = payload.get("progress_percent", 0)
                        text_msg = payload.get("status_message", "")
                        line = f"  [{pct:>3}%] {text_msg}"
                        if line != last_msg:
                            click.echo(line)
                            last_msg = line
                        if payload.get("status") in ("completed", "failed", "cancelled"):
                            break
                    except json.JSONDecodeError:
                        pass
            ps.unsubscribe(job_channel(job.id))
            ps.close()
            r.close()
        except Exception:
            pass

    listener = threading.Thread(target=_listen_progress, daemon=True)
    listener.start()
    try:
        actor.fn(job.id)
    finally:
        stop_evt.set()
        listener.join(timeout=1.5)

    db.expire_all()
    refreshed = db.get(GenerationJob, job.id)
    if not refreshed:
        raise click.ClickException(f"Job {job.id} vanished unexpectedly.")
    return refreshed


def _print_job_failure(db: Session, job: GenerationJob) -> None:
    click.secho(
        f"Job FAILED [{job.error_code or 'ERROR'}]: {job.error_message}",
        fg="red",
        err=True,
    )
    attempts = db.scalars(
        select(GenerationAttempt)
        .where(GenerationAttempt.job_id == job.id)
        .order_by(GenerationAttempt.attempt_number.asc())
    ).all()
    for att in attempts:
        click.echo(
            f"  Attempt #{att.attempt_number}: status={att.status} http={att.http_status} "
            f"code={att.upstream_error_code or '-'} ({att.duration_ms}ms) -> {att.error_message}",
            err=True,
        )


def _upsert_provider(
    db: Session,
    owner_id: str,
    provider_key: str,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model_default: Optional[str] = None,
    config_updates: Optional[dict] = None,
) -> None:
    row = db.scalars(
        select(ProviderSetting).where(
            ProviderSetting.owner_id == owner_id,
            ProviderSetting.provider_key == provider_key,
        )
    ).first()
    if not row:
        row = ProviderSetting(owner_id=owner_id, provider_key=provider_key, config_json={})
        db.add(row)
    if base_url is not None:
        row.base_url = base_url
    if api_key is not None:
        row.api_key_encrypted = encrypt_secret(api_key.strip()) if api_key.strip() else None
    if model_default is not None:
        row.model_default = model_default
    if config_updates:
        merged = dict(row.config_json or {})
        merged.update(config_updates)
        row.config_json = merged


@click.group()
@click.option("--verbose", is_flag=True, help="Show detailed JSON debug/info logs.")
def cli(verbose: bool) -> None:
    """AI Video Studio CLI — Run scripts, continuity images, Omni 1.2 videos & FFmpeg renders from terminal."""
    configure_logging(logging.INFO if verbose else logging.WARNING)


@cli.command()
def doctor() -> None:
    """Check PostgreSQL, Redis, FFmpeg/FFprobe, disk space, and provider config status."""
    settings = get_settings()
    db_ok = False
    table_count = 0
    try:
        db, owner = _get_db_and_owner()
        db.execute(text("SELECT 1"))
        table_count = db.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar_one()
        runtime = get_effective_runtime_config(db, owner_id=owner.id)
        db.close()
        db_ok = True
    except Exception as exc:
        runtime = None
        click.secho(f"Database ERROR: {exc}", fg="red")

    redis_ok = False
    try:
        r = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        redis_ok = bool(r.ping())
        r.close()
    except Exception:
        redis_ok = False

    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")
    disk = get_disk_status()

    click.secho("=== AI VIDEO STUDIO DOCTOR ===", bold=True)
    click.echo(f"PostgreSQL   : {'OK' if db_ok else 'ERROR'} ({table_count} tables)")
    click.echo(f"Redis        : {'OK' if redis_ok else 'ERROR'} ({settings.redis_url})")
    click.echo(f"FFmpeg       : {ffmpeg_path or 'MISSING'}")
    click.echo(f"FFprobe      : {ffprobe_path or 'MISSING'}")
    click.echo(
        f"Disk Free    : {disk['free_gb']} GB / {disk['total_gb']} GB "
        f"({'HEALTHY' if disk['healthy'] else 'LOW DISK'})"
    )
    if runtime:
        click.echo(
            f"Flow2API     : {runtime.flow2api_base_url} | key={mask_secret(runtime.flow2api_api_key) or '(not set)'}"
        )
        click.echo(
            f"Script LLM   : {runtime.script_llm_provider} ({runtime.script_llm_model}) | "
            f"key={mask_secret(runtime.script_llm_api_key) or '(template_fallback)'}"
        )
        caption_status = (
            f"{runtime.caption_llm_provider} ({runtime.caption_llm_model})"
            if runtime.caption_llm_enabled
            else "disabled"
        )
        click.echo(f"Caption LLM  : {caption_status}")
        click.echo(
            f"Storage      : images={runtime.storage_image_backend}, videos={runtime.storage_video_backend}"
        )
    if not (db_ok and redis_ok and ffmpeg_path and ffprobe_path):
        raise SystemExit(1)


# --- Config ---
@cli.group()
def config() -> None:
    """View or configure Flow2API, Script LLM, Cloudinary, R2, and Worker settings."""


@config.command("show")
def config_show() -> None:
    """Display active effective configuration (secrets masked)."""
    db, owner = _get_db_and_owner()
    try:
        cfg = get_effective_runtime_config(db, owner_id=owner.id)
        click.echo(
            json.dumps(
                {
                    "flow2api": {
                        "base_url": cfg.flow2api_base_url,
                        "api_key": mask_secret(cfg.flow2api_api_key),
                        "image_timeout": cfg.flow2api_image_timeout,
                        "video_timeout": cfg.flow2api_video_timeout,
                    },
                    "script_llm": {
                        "provider": cfg.script_llm_provider,
                        "base_url": cfg.script_llm_base_url,
                        "api_key": mask_secret(cfg.script_llm_api_key),
                        "model": cfg.script_llm_model,
                    },
                    "caption_llm": {
                        "enabled": cfg.caption_llm_enabled,
                        "provider": cfg.caption_llm_provider,
                        "base_url": cfg.caption_llm_base_url,
                        "api_key": mask_secret(cfg.caption_llm_api_key),
                        "model": cfg.caption_llm_model,
                    },
                    "storage": {
                        "image_backend": cfg.storage_image_backend,
                        "video_backend": cfg.storage_video_backend,
                        "cloudinary_cloud_name": cfg.cloudinary_cloud_name,
                        "r2_bucket_name": cfg.r2_bucket_name,
                    },
                    "worker": {
                        "video_concurrency": cfg.worker_video_concurrency,
                        "image_concurrency": cfg.worker_image_concurrency,
                        "request_delay_seconds": cfg.worker_request_delay_seconds,
                    },
                },
                indent=2,
            )
        )
    finally:
        db.close()


@config.command("set")
@click.option("--flow2api-url", default=None)
@click.option("--flow2api-key", default=None)
@click.option("--image-timeout", type=int, default=None)
@click.option("--video-timeout", type=int, default=None)
@click.option("--llm-provider", type=click.Choice(["openai_compatible", "gemini"]), default=None)
@click.option("--llm-url", default=None)
@click.option("--llm-key", default=None)
@click.option("--llm-model", default=None)
@click.option(
    "--caption-llm-provider", "caption_llm_provider", type=click.Choice(["openai_compatible", "gemini"]), default=None
)
@click.option("--caption-llm-url", default=None, help="Base URL of an OpenAI-compatible vision model endpoint.")
@click.option("--caption-llm-key", default=None)
@click.option("--caption-llm-model", default=None, help="Open-weight vision model id, e.g. Qwen2-VL-7B-Instruct.")
@click.option(
    "--enable-caption/--disable-caption",
    "caption_enabled",
    default=None,
    help="Turn auto image-description (character/background) on or off.",
)
@click.option("--cloudinary-cloud", default=None)
@click.option("--cloudinary-key", default=None)
@click.option("--cloudinary-secret", default=None)
@click.option("--r2-endpoint", default=None)
@click.option("--r2-key", default=None)
@click.option("--r2-secret", default=None)
@click.option("--r2-bucket", default=None)
@click.option("--r2-public-url", default=None)
@click.option("--video-concurrency", type=int, default=None)
@click.option("--request-delay", type=int, default=None)
def config_set(
    flow2api_url,
    flow2api_key,
    image_timeout,
    video_timeout,
    llm_provider,
    llm_url,
    llm_key,
    llm_model,
    caption_llm_provider,
    caption_llm_url,
    caption_llm_key,
    caption_llm_model,
    caption_enabled,
    cloudinary_cloud,
    cloudinary_key,
    cloudinary_secret,
    r2_endpoint,
    r2_key,
    r2_secret,
    r2_bucket,
    r2_public_url,
    video_concurrency,
    request_delay,
) -> None:
    """Persist provider keys & limits encrypted in PostgreSQL."""
    db, owner = _get_db_and_owner()
    try:
        if any(x is not None for x in (flow2api_url, flow2api_key, image_timeout, video_timeout)):
            cfg = {}
            if image_timeout is not None:
                cfg["image_timeout"] = image_timeout
            if video_timeout is not None:
                cfg["video_timeout"] = video_timeout
            _upsert_provider(
                db,
                owner.id,
                "flow2api",
                base_url=normalize_v1_base_url(flow2api_url) if flow2api_url else None,
                api_key=flow2api_key,
                config_updates=cfg or None,
            )

        if any(x is not None for x in (llm_provider, llm_url, llm_key, llm_model)):
            _upsert_provider(
                db,
                owner.id,
                "script_llm",
                base_url=llm_url,
                api_key=llm_key,
                model_default=llm_model,
                config_updates={"provider": llm_provider} if llm_provider else None,
            )

        if any(
            x is not None
            for x in (caption_llm_provider, caption_llm_url, caption_llm_key, caption_llm_model, caption_enabled)
        ):
            cap_cfg = {}
            if caption_llm_provider:
                cap_cfg["provider"] = caption_llm_provider
            if caption_enabled is not None:
                cap_cfg["enabled"] = caption_enabled
            _upsert_provider(
                db,
                owner.id,
                "caption_llm",
                base_url=caption_llm_url,
                api_key=caption_llm_key,
                model_default=caption_llm_model,
                config_updates=cap_cfg or None,
            )

        if any(x is not None for x in (cloudinary_cloud, cloudinary_key, cloudinary_secret)):
            cfg = {"enabled": True}
            if cloudinary_cloud is not None:
                cfg["cloud_name"] = cloudinary_cloud
            if cloudinary_key is not None:
                cfg["api_key"] = cloudinary_key
            _upsert_provider(
                db,
                owner.id,
                "cloudinary",
                api_key=cloudinary_secret,
                config_updates=cfg,
            )

        if any(x is not None for x in (r2_endpoint, r2_key, r2_secret, r2_bucket, r2_public_url)):
            cfg = {"enabled": True}
            if r2_key is not None:
                cfg["access_key_id"] = r2_key
            if r2_bucket is not None:
                cfg["bucket_name"] = r2_bucket
            if r2_public_url is not None:
                cfg["public_base_url"] = r2_public_url
            _upsert_provider(
                db,
                owner.id,
                "r2",
                base_url=r2_endpoint,
                api_key=r2_secret,
                config_updates=cfg,
            )

        if video_concurrency is not None or request_delay is not None:
            cfg = {}
            if video_concurrency is not None:
                cfg["video_concurrency"] = max(1, min(10, video_concurrency))
            if request_delay is not None:
                cfg["request_delay_seconds"] = max(0, min(120, request_delay))
            _upsert_provider(db, owner.id, "worker", config_updates=cfg)

        db.commit()
        click.secho("Configuration saved to database.", fg="green")
    finally:
        db.close()


@cli.command("test-api")
def test_api() -> None:
    """Test Flow2API connection using GET /v1/models."""
    db, owner = _get_db_and_owner()
    try:
        cfg = get_effective_runtime_config(db, owner_id=owner.id)
        client = Flow2APIClient(
            base_url=cfg.flow2api_base_url,
            api_key=cfg.flow2api_api_key,
            image_timeout=cfg.flow2api_image_timeout,
            video_timeout=cfg.flow2api_video_timeout,
        )
        res = client.test_connection()
        click.secho(
            f"Flow2API OK: {res['total_models']} models "
            f"(image={res['image_models']}, video={res['video_models']})",
            fg="green",
        )
        click.echo(f"Samples: {', '.join(res['sample_models'])}")
    except Flow2APIUpstreamError as exc:
        click.secho(f"Flow2API FAILED [{exc.error_code or exc.status_code}]: {exc}", fg="red", err=True)
        raise SystemExit(1)
    finally:
        db.close()


@cli.command()
@click.option("--refresh", is_flag=True, help="Force live sync from GET /v1/models.")
@click.option("--type", "kind", type=click.Choice(["all", "image", "video"]), default="all")
def models(refresh: bool, kind: str) -> None:
    """List Flow2API image & video models (live synced when key is set, else catalog defaults)."""
    db, owner = _get_db_and_owner()
    try:
        cfg = get_effective_runtime_config(db, owner_id=owner.id)
        source = "catalog_default"
        model_list = DEFAULT_FLOW2API_CATALOG
        if refresh or (cfg.flow2api_api_key and not cfg.flow2api_cached_models):
            client = Flow2APIClient(
                base_url=cfg.flow2api_base_url,
                api_key=cfg.flow2api_api_key,
            )
            model_list = client.list_models()
            _upsert_provider(db, owner.id, "flow2api", config_updates={"cached_models": model_list})
            db.commit()
            source = "flow2api_live"
        elif cfg.flow2api_cached_models:
            model_list = cfg.flow2api_cached_models
            source = "flow2api_cached"

        click.echo(f"Model source: {source}")
        for m in model_list:
            mtype = m.get("type", "other")
            if kind != "all" and mtype != kind:
                continue
            click.echo(f"  [{mtype:<5}] {m.get('id'):<42} - {m.get('description', '')}")
    except Flow2APIUpstreamError as exc:
        click.secho(f"Failed syncing models: {exc}", fg="red", err=True)
        raise SystemExit(1)
    finally:
        db.close()


# --- Characters ---
@cli.group()
def character() -> None:
    """Manage Character Library and character consistency rules."""


@character.command("list")
def character_list() -> None:
    db, owner = _get_db_and_owner()
    try:
        rows = db.scalars(select(Character).where(Character.owner_id == owner.id)).all()
        if not rows:
            click.echo("No characters yet. Use: ./studio character add --name ... --image ...")
            return
        for c in rows:
            mark = " [default]" if c.is_default else ""
            click.echo(f"{c.id} | {c.name}{mark} | role={c.role_title} | voice={c.voice_style}")
    finally:
        db.close()


@character.command("add")
@click.option("--name", required=True)
@click.option("--image", required=True, help="Local image path or HTTPS URL.")
@click.option("--extra-ref", multiple=True, help="Additional angle reference image path/URL.")
@click.option("--description", default="", help="Visual description; auto-generated from --image when blank and Caption LLM is enabled.")
@click.option("--age", default=None)
@click.option("--voice", default="Mature clear presenter voice")
@click.option("--role", default="Presenter")
@click.option("--screen-time", type=int, default=30)
@click.option("--clothing-rule", default="Preserve original face and clothing")
@click.option("--default", "is_default", is_flag=True)
def character_add(
    name: str,
    image: str,
    extra_ref: tuple[str, ...],
    description: str,
    age: Optional[str],
    voice: str,
    role: str,
    screen_time: int,
    clothing_rule: str,
    is_default: bool,
) -> None:
    """Import a character with reference photo(s) and consistency rules."""
    db, owner = _get_db_and_owner()
    try:
        storage = MediaStorageService.from_db(db, owner_id=owner.id)
        primary_asset = _ingest_media_source(db, owner_id=owner.id, storage=storage, source=image)

        final_description = description.strip()
        if not final_description and storage.cfg and storage.cfg.caption_llm_enabled:
            caption = generate_image_caption(
                storage.read_asset_bytes(primary_asset), storage.cfg, subject_hint="presenter character"
            )
            if caption:
                final_description = caption
                click.secho(f"  Auto-description: {caption}", fg="cyan")

        if is_default:
            for existing in db.scalars(
                select(Character).where(Character.owner_id == owner.id, Character.is_default.is_(True))
            ).all():
                existing.is_default = False

        char = Character(
            owner_id=owner.id,
            name=name.strip(),
            description=final_description,
            age_appearance=age,
            voice_style=voice.strip(),
            role_title=role.strip(),
            default_screen_time_seconds=screen_time,
            primary_image_asset_id=primary_asset.id,
            is_default=is_default,
        )
        db.add(char)
        db.flush()
        db.add(
            CharacterReference(
                character_id=char.id,
                media_asset_id=primary_asset.id,
                angle_label="front",
                is_primary=True,
                sort_order=0,
            )
        )
        for idx, extra in enumerate(extra_ref, start=1):
            extra_asset = _ingest_media_source(db, owner_id=owner.id, storage=storage, source=extra)
            db.add(
                CharacterReference(
                    character_id=char.id,
                    media_asset_id=extra_asset.id,
                    angle_label=f"angle_{idx}",
                    is_primary=False,
                    sort_order=idx,
                )
            )
        db.add(
            CharacterRule(
                owner_id=owner.id,
                character_id=char.id,
                clothing_rule=clothing_rule,
            )
        )
        db.commit()
        click.secho(f"Character created: {char.id} ({char.name})", fg="green")
        click.echo(f"  Primary image: {_asset_location_hint(storage, primary_asset)}")
    finally:
        db.close()


# --- Backgrounds ---
@cli.group()
def background() -> None:
    """Manage Background Library and preservation rules."""


@background.command("list")
def background_list() -> None:
    db, owner = _get_db_and_owner()
    try:
        rows = db.scalars(select(Background).where(Background.owner_id == owner.id)).all()
        if not rows:
            click.echo("No backgrounds yet. Use: ./studio background add --name ... --image ...")
            return
        for b in rows:
            click.echo(f"{b.id} | {b.name} | style={b.style}")
    finally:
        db.close()


@background.command("add")
@click.option("--name", required=True)
@click.option("--image", required=True, help="Local image path or HTTPS URL.")
@click.option("--style", default="photorealistic")
@click.option("--description", default="", help="Visual description; auto-generated from --image when blank and Caption LLM is enabled.")
@click.option("--custom-rules", default="")
def background_add(name: str, image: str, style: str, description: str, custom_rules: str) -> None:
    """Import a background reference image with preservation rules."""
    db, owner = _get_db_and_owner()
    try:
        storage = MediaStorageService.from_db(db, owner_id=owner.id)
        asset = _ingest_media_source(db, owner_id=owner.id, storage=storage, source=image)

        final_description = description.strip()
        if not final_description and storage.cfg and storage.cfg.caption_llm_enabled:
            caption = generate_image_caption(
                storage.read_asset_bytes(asset), storage.cfg, subject_hint="studio background environment"
            )
            if caption:
                final_description = caption
                click.secho(f"  Auto-description: {caption}", fg="cyan")

        bg = Background(
            owner_id=owner.id,
            name=name.strip(),
            description=final_description,
            style=style.strip(),
            media_asset_id=asset.id,
            cloudinary_url=asset.public_url,
            preservation_rules_json={
                "keep_desk_position": True,
                "keep_camera_angle": True,
                "keep_monitors": True,
                "keep_lighting": True,
                "keep_room_layout": True,
                "seat_character_properly": True,
                "no_extra_objects": True,
                "custom_rules": custom_rules.strip(),
            },
        )
        db.add(bg)
        db.commit()
        click.secho(f"Background created: {bg.id} ({bg.name})", fg="green")
        click.echo(f"  Image stored: {_asset_location_hint(storage, asset)}")
    finally:
        db.close()


# --- Projects ---
@cli.group()
def project() -> None:
    """Create and inspect video projects."""


@project.command("list")
def project_list() -> None:
    db, owner = _get_db_and_owner()
    try:
        rows = db.scalars(
            select(Project).where(Project.owner_id == owner.id).order_by(Project.updated_at.desc())
        ).all()
        if not rows:
            click.echo("No projects yet. Use: ./studio project create --name ... --topic ...")
            return
        for p in rows:
            click.echo(
                f"{p.id} | {p.name} | {p.aspect_ratio} | {p.target_duration_seconds}s | status={p.status}"
            )
    finally:
        db.close()


@project.command("show")
@click.argument("project_ref")
def project_show(project_ref: str) -> None:
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        click.echo(
            json.dumps(
                {
                    "id": proj.id,
                    "name": proj.name,
                    "topic": proj.topic,
                    "language": proj.language,
                    "target_duration_seconds": proj.target_duration_seconds,
                    "aspect_ratio": proj.aspect_ratio,
                    "image_model": proj.image_model,
                    "video_model": proj.video_model,
                    "voice_mode": proj.voice_mode,
                    "auto_approve_master": proj.auto_approve_master,
                    "status": proj.status,
                    "character_schedule": proj.character_schedule_json,
                },
                indent=2,
            )
        )
    finally:
        db.close()


@project.command("create")
@click.option("--name", required=True)
@click.option("--topic", required=True)
@click.option("--category", default="education")
@click.option("--language", default="en-US")
@click.option("--duration", type=int, default=24)
@click.option("--aspect", type=click.Choice(["9:16", "16:9", "1:1"]), default="9:16")
@click.option("--style", default="photorealistic")
@click.option("--char-id", default=None, help="Character ID or name.")
@click.option("--bg-id", default=None, help="Background ID or name.")
@click.option("--image-model", default="gemini-3.1-flash-image-portrait-2k")
@click.option("--video-model", default="omni-1.2-flash-8s-portrait")
@click.option("--voice-mode", type=click.Choice(["ai_generated", "external_tts"]), default="ai_generated")
@click.option("--schedule", multiple=True, help='Optional slot e.g. "0-24:CHAR_REF" or "24-40:CHAR_REF".')
@click.option("--auto-approve-master", is_flag=True)
def project_create(
    name: str,
    topic: str,
    category: str,
    language: str,
    duration: int,
    aspect: str,
    style: str,
    char_id: Optional[str],
    bg_id: Optional[str],
    image_model: str,
    video_model: str,
    voice_mode: str,
    schedule: tuple[str, ...],
    auto_approve_master: bool,
) -> None:
    """Create a new AI Video Studio project."""
    db, owner = _get_db_and_owner()
    try:
        chars = db.scalars(select(Character).where(Character.owner_id == owner.id)).all()
        bgs = db.scalars(select(Background).where(Background.owner_id == owner.id)).all()

        resolved_char = _resolve_by_id_or_name(chars, char_id, "Character").id if char_id else None
        resolved_bg = _resolve_by_id_or_name(bgs, bg_id, "Background").id if bg_id else None

        sched_list: list[dict] = []
        for raw_slot in schedule:
            try:
                span, c_ref = raw_slot.split(":", 1)
                start_s, end_s = span.split("-", 1)
                slot_char = _resolve_by_id_or_name(chars, c_ref, "Character").id
                sched_list.append(
                    {
                        "start_sec": float(start_s),
                        "end_sec": float(end_s),
                        "character_id": slot_char,
                        "background_id": resolved_bg,
                    }
                )
            except Exception as exc:
                raise click.ClickException(
                    f"Invalid --schedule '{raw_slot}'. Expected format '0-24:CHAR_ID_OR_NAME'."
                ) from exc

        proj = Project(
            owner_id=owner.id,
            name=name.strip(),
            topic_category=category.strip(),
            topic=topic.strip(),
            language=language.strip(),
            target_duration_seconds=duration,
            aspect_ratio=aspect,
            visual_style=style.strip(),
            default_character_id=resolved_char,
            default_background_id=resolved_bg,
            image_model=image_model.strip(),
            video_model=video_model.strip(),
            voice_mode=voice_mode,
            character_schedule_json=sched_list,
            auto_approve_master=auto_approve_master,
        )
        db.add(proj)
        db.commit()
        click.secho(f"Project created: {proj.id} ({proj.name})", fg="green")
    finally:
        db.close()


# --- Scripts ---
@cli.group()
def script() -> None:
    """Generate and inspect AI scripts and planned scenes."""


def _generate_script_in_db(
    db: Session,
    owner_id: str,
    proj: Project,
    *,
    topic_override: Optional[str] = None,
    duration_override: Optional[int] = None,
    force_overwrite_locked: bool = False,
) -> Script:
    runtime = get_effective_runtime_config(db, owner_id=owner_id)
    target_duration = duration_override or proj.target_duration_seconds
    clip_dur = parse_model_clip_duration(proj.video_model, default=8)
    windows = plan_scene_windows(
        target_duration_sec=target_duration,
        clip_duration_sec=clip_dur,
        character_schedule=proj.character_schedule_json,
        default_character_id=proj.default_character_id,
        default_background_id=proj.default_background_id,
    )
    chars = db.scalars(select(Character).where(Character.owner_id == owner_id)).all()
    char_names = {c.id: c.name for c in chars}

    draft = generate_script_draft(
        runtime_cfg=runtime,
        topic=topic_override or proj.topic,
        topic_category=proj.topic_category,
        goal_description=proj.goal_description,
        language=proj.language,
        visual_style=proj.visual_style,
        windows=windows,
        character_names=char_names,
        voice_mode=proj.voice_mode,
        script_mode=proj.script_mode,
    )

    prev_scripts = db.scalars(
        select(Script).where(Script.project_id == proj.id).order_by(Script.version.desc())
    ).all()
    prev_active = next((s for s in prev_scripts if s.is_active), None)
    locked_by_idx: dict[int, Scene] = {}
    if prev_active and not force_overwrite_locked:
        for ps in db.scalars(select(Scene).where(Scene.script_id == prev_active.id)).all():
            if ps.is_locked or ps.approval_status == "approved":
                locked_by_idx[ps.scene_index] = ps

    next_ver = (prev_scripts[0].version if prev_scripts else 0) + 1
    for ps in prev_scripts:
        ps.is_active = False

    sc_row = Script(
        project_id=proj.id,
        version=next_ver,
        is_active=True,
        title=draft.title,
        objective=draft.objective,
        opening_hook=draft.opening_hook,
        narration_summary=draft.narration_summary,
        total_estimated_seconds=float(sum(w.duration_sec for w in windows)),
        llm_provider_used=draft.llm_provider_used,
        raw_payload_json=draft.raw_payload,
    )
    db.add(sc_row)
    db.flush()

    for win, sc_draft in zip(windows, draft.scenes):
        locked_prev = locked_by_idx.get(win.scene_index)
        dialogue = locked_prev.dialogue if locked_prev else sc_draft.dialogue
        dur = locked_prev.planned_duration_sec if locked_prev else win.duration_sec
        est, warn = compute_scene_speech_metrics(dialogue, dur, proj.language)
        scene = Scene(
            project_id=proj.id,
            script_id=sc_row.id,
            scene_index=win.scene_index,
            title=locked_prev.title if locked_prev else sc_draft.title,
            start_time_sec=win.start_time_sec,
            end_time_sec=win.end_time_sec,
            planned_duration_sec=dur,
            character_id=locked_prev.character_id if locked_prev else win.character_id,
            background_id=locked_prev.background_id if locked_prev else win.background_id,
            dialogue=dialogue,
            caption_text=locked_prev.caption_text if locked_prev else sc_draft.caption_text,
            estimated_speech_sec=est,
            speech_warning=warn,
            character_action=locked_prev.character_action if locked_prev else sc_draft.character_action,
            camera_motion=locked_prev.camera_motion if locked_prev else sc_draft.camera_motion,
            transition_type=locked_prev.transition_type if locked_prev else sc_draft.transition_type,
            is_locked=bool(locked_prev),
            is_master_scene=win.is_master_scene,
        )
        db.add(scene)
        db.flush()
        create_scene_prompts_for_scene(db, project=proj, scene=scene)

    proj.status = "script_ready"
    db.commit()
    return sc_row


def _print_scenes_table(db: Session, script_id: str) -> None:
    scenes = db.scalars(
        select(Scene).where(Scene.script_id == script_id).order_by(Scene.scene_index.asc())
    ).all()
    for sc in scenes:
        master = " [MASTER]" if sc.is_master_scene else ""
        lock = " [LOCKED]" if sc.is_locked else ""
        warn = " (!SPEECH)" if sc.speech_warning else ""
        click.echo(
            f"  #{sc.scene_index:02d} {sc.id[:8]} | {sc.start_time_sec:04.1f}-{sc.end_time_sec:04.1f}s "
            f"| status={sc.status:<14} | appr={sc.approval_status:<17}{master}{lock}{warn}"
        )
        click.echo(f"      Dialogue: {sc.dialogue}")


@script.command("generate")
@click.argument("project_ref")
@click.option("--duration", type=int, default=None)
@click.option("--topic", default=None)
@click.option("--force-unlock", is_flag=True)
def script_generate(
    project_ref: str, duration: Optional[int], topic: Optional[str], force_unlock: bool
) -> None:
    """Generate a multi-scene script and 8-layer Image/Video prompts for a project."""
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        sc_row = _generate_script_in_db(
            db,
            owner.id,
            proj,
            topic_override=topic,
            duration_override=duration,
            force_overwrite_locked=force_unlock,
        )
        click.secho(
            f"Script v{sc_row.version} generated ({sc_row.llm_provider_used}): {sc_row.title}",
            fg="green",
        )
        _print_scenes_table(db, sc_row.id)
    finally:
        db.close()


@script.command("show")
@click.argument("project_ref")
@click.option("--prompts", is_flag=True, help="Also print Image & Video 8-layer prompts.")
def script_show(project_ref: str, prompts: bool) -> None:
    """Display active script scenes (and optionally all prompts)."""
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        active = db.scalars(
            select(Script)
            .where(Script.project_id == proj.id, Script.is_active.is_(True))
            .order_by(Script.version.desc())
        ).first()
        if not active:
            click.echo("No active script. Run: ./studio script generate <project>")
            return
        click.secho(f"Script v{active.version}: {active.title} ({active.llm_provider_used})", bold=True)
        _print_scenes_table(db, active.id)
        if prompts:
            scenes = db.scalars(
                select(Scene).where(Scene.script_id == active.id).order_by(Scene.scene_index.asc())
            ).all()
            for sc in scenes:
                click.secho(f"\n--- Scene #{sc.scene_index:02d} ({sc.id}) ---", bold=True)
                for p in db.scalars(
                    select(ScenePrompt).where(ScenePrompt.scene_id == sc.id, ScenePrompt.is_active.is_(True))
                ).all():
                    click.secho(f"[{p.prompt_type.upper()} PROMPT]", fg="cyan")
                    click.echo(p.compiled_prompt)
    finally:
        db.close()


# --- Scenes ---
@cli.group()
def scene() -> None:
    """Inspect, edit, approve, generate images/videos, or rollback scenes."""


@scene.command("list")
@click.argument("project_ref")
def scene_list(project_ref: str) -> None:
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        active = db.scalars(
            select(Script)
            .where(Script.project_id == proj.id, Script.is_active.is_(True))
            .order_by(Script.version.desc())
        ).first()
        if not active:
            click.echo("No active script.")
            return
        _print_scenes_table(db, active.id)
    finally:
        db.close()


@scene.command("prompts")
@click.argument("scene_ref")
def scene_prompts(scene_ref: str) -> None:
    """Print 8-layer Image Prompt A and Video Prompt B for a scene."""
    db, owner = _get_db_and_owner()
    try:
        sc, _ = _resolve_scene(db, owner.id, scene_ref)
        prompts = db.scalars(
            select(ScenePrompt).where(ScenePrompt.scene_id == sc.id, ScenePrompt.is_active.is_(True))
        ).all()
        for p in prompts:
            click.secho(f"\n=== {p.prompt_type.upper()} PROMPT (v{p.version}) ===", fg="cyan", bold=True)
            for layer_key in PROMPT_LAYER_KEYS:
                val = (p.layers_json or {}).get(layer_key, "")
                if val:
                    lock_tag = " [LOCKED]" if layer_key in (p.locked_layers_json or []) else ""
                    click.echo(f"  {layer_key}{lock_tag}: {val}")
            click.secho("-- Compiled --", bold=True)
            click.echo(p.compiled_prompt)
    finally:
        db.close()


@scene.command("edit")
@click.argument("scene_ref")
@click.option("--dialogue", default=None)
@click.option("--action", default=None)
@click.option("--lock/--unlock", "lock_flag", default=None)
@click.option("--auto-fit", is_flag=True, help="Trim dialogue to fit clip duration budget.")
def scene_edit(
    scene_ref: str,
    dialogue: Optional[str],
    action: Optional[str],
    lock_flag: Optional[bool],
    auto_fit: bool,
) -> None:
    """Edit scene dialogue/action, lock/unlock, and refresh unlocked prompt layers."""
    db, owner = _get_db_and_owner()
    try:
        sc, proj = _resolve_scene(db, owner.id, scene_ref)
        if lock_flag is not None:
            sc.is_locked = lock_flag
        if dialogue is not None:
            sc.dialogue = dialogue
            sc.caption_text = dialogue
        if action is not None:
            sc.character_action = action
        dur = sc.actual_duration_sec or sc.planned_duration_sec
        if auto_fit:
            sc.dialogue = trim_dialogue_to_fit(sc.dialogue, dur, proj.language)
            sc.caption_text = sc.dialogue
        est, warn = compute_scene_speech_metrics(sc.dialogue, dur, proj.language)
        sc.estimated_speech_sec = est
        sc.speech_warning = warn

        # Rebuild active unlocked prompts
        char = db.get(Character, sc.character_id or proj.default_character_id) if (sc.character_id or proj.default_character_id) else None
        rule = db.scalars(select(CharacterRule).where(CharacterRule.character_id == char.id)).first() if char else None
        bg = db.get(Background, sc.background_id or proj.default_background_id) if (sc.background_id or proj.default_background_id) else None

        for p in db.scalars(select(ScenePrompt).where(ScenePrompt.scene_id == sc.id, ScenePrompt.is_active.is_(True))).all():
            if p.is_manual_override:
                continue
            if p.prompt_type == "image":
                layers, compiled = build_image_prompt_layers(
                    scene=sc, project=proj, character=char, char_rule=rule, background=bg,
                    is_master_scene=sc.is_master_scene, existing_layers=p.layers_json, locked_layers=p.locked_layers_json,
                )
            else:
                layers, compiled = build_video_prompt_layers(
                    scene=sc, project=proj, character=char, char_rule=rule, background=bg,
                    existing_layers=p.layers_json, locked_layers=p.locked_layers_json,
                )
            p.layers_json = layers
            p.compiled_prompt = compiled

        db.commit()
        click.secho(f"Scene #{sc.scene_index:02d} updated (speech est={est:.1f}s).", fg="green")
        if warn:
            click.secho(f"Warning: {warn}", fg="yellow")
    finally:
        db.close()


@scene.command("approve")
@click.argument("scene_ref")
def scene_approve(scene_ref: str) -> None:
    """Approve a scene image (required on Scene 01 Master Image before Scene 02+)."""
    db, owner = _get_db_and_owner()
    try:
        sc, _ = _resolve_scene(db, owner.id, scene_ref)
        if not get_active_scene_image_asset(db, sc.id):
            raise click.ClickException("Cannot approve scene before an image is generated.")
        flagged = approve_scene_image(db, sc)
        db.commit()
        click.secho(f"Scene #{sc.scene_index:02d} approved!", fg="green")
        if flagged:
            click.secho(f"Flagged downstream scenes as needs_review: {', '.join(flagged)}", fg="yellow")
    finally:
        db.close()


@scene.command("rollback")
@click.argument("scene_ref")
@click.argument("scene_asset_id")
def scene_rollback(scene_ref: str, scene_asset_id: str) -> None:
    """Activate a previous SceneAsset version on a scene."""
    db, owner = _get_db_and_owner()
    try:
        sc, _ = _resolve_scene(db, owner.id, scene_ref)
        target = activate_scene_asset_version(db, sc.id, scene_asset_id)
        db.commit()
        click.secho(f"Activated {target.asset_role} v{target.version} on scene #{sc.scene_index:02d}", fg="green")
    finally:
        db.close()


@scene.command("gen-image")
@click.argument("scene_ref")
@click.option("--model", default=None)
@click.option("--enqueue", is_flag=True, help="Queue to Dramatiq worker instead of running inline.")
def scene_gen_image(scene_ref: str, model: Optional[str], enqueue: bool) -> None:
    """Generate image for a scene via Flow2API (respecting Master Image Continuity Gate)."""
    db, owner = _get_db_and_owner()
    try:
        sc, proj = _resolve_scene(db, owner.id, scene_ref)
        try:
            validate_master_gate_for_scene(db, proj, sc)
        except ContinuityGateError as exc:
            raise click.ClickException(str(exc)) from exc

        chosen_model = (model or proj.image_model).strip()
        job = GenerationJob(
            owner_id=owner.id,
            project_id=proj.id,
            scene_id=sc.id,
            job_type="scene_image",
            provider_model=chosen_model,
            status="queued",
            status_message=f"Queued image generation ({chosen_model})",
            max_attempts=3,
            idempotency_key=f"cli-img:{sc.id}:{uuid.uuid4().hex[:8]}",
        )
        sc.status = "image_queued"
        db.add(job)
        db.commit()

        click.echo(f"Running image generation for Scene #{sc.scene_index:02d} ({chosen_model})...")
        done = _execute_job(db, job, run_scene_image_job, enqueue=enqueue)
        if enqueue:
            return
        if done.status != "completed":
            _print_job_failure(db, done)
            raise SystemExit(1)

        storage = MediaStorageService.from_db(db, owner_id=owner.id)
        ma = db.get(MediaAsset, done.result_json.get("media_asset_id"))
        click.secho(
            f"Image ready ({ma.width if ma else '?'}x{ma.height if ma else '?'}): {_asset_location_hint(storage, ma)}",
            fg="green",
        )
    finally:
        db.close()


@scene.command("gen-video")
@click.argument("scene_ref")
@click.option("--model", default=None)
@click.option("--enqueue", is_flag=True, help="Queue to Dramatiq worker instead of running inline.")
def scene_gen_video(scene_ref: str, model: Optional[str], enqueue: bool) -> None:
    """Generate spoken video clip for an approved scene via Flow2API Omni 1.2 / Veo."""
    db, owner = _get_db_and_owner()
    try:
        sc, proj = _resolve_scene(db, owner.id, scene_ref)
        if not get_active_scene_image_asset(db, sc.id):
            raise click.ClickException("Generate a reference image for this scene first.")
        if sc.approval_status != "approved" and not proj.auto_approve_master:
            raise click.ClickException(
                f"Scene #{sc.scene_index:02d} is not approved. Run: ./studio scene approve {sc.id}"
            )

        chosen_model = (model or proj.video_model).strip()
        job = GenerationJob(
            owner_id=owner.id,
            project_id=proj.id,
            scene_id=sc.id,
            job_type="scene_video",
            provider_model=chosen_model,
            status="queued",
            status_message=f"Queued video generation ({chosen_model})",
            max_attempts=3,
            idempotency_key=f"cli-vid:{sc.id}:{uuid.uuid4().hex[:8]}",
        )
        sc.status = "video_queued"
        db.add(job)
        db.commit()

        click.echo(f"Running video generation for Scene #{sc.scene_index:02d} ({chosen_model})...")
        done = _execute_job(db, job, run_scene_video_job, enqueue=enqueue)
        if enqueue:
            return
        if done.status != "completed":
            _print_job_failure(db, done)
            raise SystemExit(1)

        storage = MediaStorageService.from_db(db, owner_id=owner.id)
        ma = db.get(MediaAsset, done.result_json.get("media_asset_id"))
        dur = done.result_json.get("actual_duration_sec")
        click.secho(
            f"Video ready (duration={dur}s): {_asset_location_hint(storage, ma)}",
            fg="green",
        )
    finally:
        db.close()


# --- Render ---
def _trigger_project_render(
    db: Session,
    owner_id: str,
    proj: Project,
    *,
    transition: Optional[str] = None,
    burn_subtitles: Optional[bool] = None,
    color_grade: Optional[bool] = None,
    bgm_source: Optional[str] = None,
    enqueue: bool = False,
) -> Render:
    active_script = db.scalars(
        select(Script)
        .where(Script.project_id == proj.id, Script.is_active.is_(True))
        .order_by(Script.version.desc())
    ).first()
    if not active_script:
        raise click.ClickException("Project has no active script.")

    storage = MediaStorageService.from_db(db, owner_id=owner_id)
    cfg = dict(proj.timeline_config_json or {})
    cfg["script_id"] = active_script.id
    if transition is not None:
        cfg["default_transition"] = transition
    if burn_subtitles is not None:
        cfg["burn_subtitles"] = burn_subtitles
    if color_grade is not None:
        cfg["color_grade"] = color_grade
    if bgm_source:
        bgm_asset = _ingest_media_source(
            db, owner_id=owner_id, storage=storage, source=bgm_source, asset_kind="audio", project_id=proj.id
        )
        cfg["bgm_asset_id"] = bgm_asset.id

    prev_renders = db.scalars(select(Render).where(Render.project_id == proj.id)).all()
    next_ver = max((r.version for r in prev_renders), default=0) + 1
    render_row = Render(
        owner_id=owner_id,
        project_id=proj.id,
        version=next_ver,
        status="queued",
        render_config_json=cfg,
    )
    db.add(render_row)
    db.flush()

    job = GenerationJob(
        owner_id=owner_id,
        project_id=proj.id,
        render_id=render_row.id,
        job_type="project_render",
        provider_model="ffmpeg",
        status="queued",
        status_message="Queued FFmpeg video composition",
        max_attempts=1,
        idempotency_key=f"cli-render:{render_row.id}:{uuid.uuid4().hex[:6]}",
    )
    db.add(job)
    db.commit()

    click.echo(f"Rendering final video v{next_ver} via FFmpeg...")
    done = _execute_job(db, job, run_project_render_job, enqueue=enqueue)
    if enqueue:
        return render_row
    if done.status != "completed":
        _print_job_failure(db, done)
        raise SystemExit(1)

    db.refresh(render_row)
    mp4 = db.get(MediaAsset, render_row.output_media_asset_id) if render_row.output_media_asset_id else None
    srt = db.get(MediaAsset, render_row.subtitle_media_asset_id) if render_row.subtitle_media_asset_id else None
    click.secho(
        f"Render completed ({render_row.resolution}, {render_row.duration_seconds:.2f}s, {render_row.file_size_bytes} bytes):",
        fg="green",
    )
    click.echo(f"  MP4: {_asset_location_hint(storage, mp4)}")
    click.echo(f"  SRT: {_asset_location_hint(storage, srt)}")
    return render_row


@cli.command("render")
@click.argument("project_ref")
@click.option("--transition", type=click.Choice(["none", "fade", "crossfade"]), default=None)
@click.option("--no-burn-sub", is_flag=True, help="Generate .srt without burning subtitles into video frames.")
@click.option(
    "--no-color-grade",
    is_flag=True,
    help="Skip the subtle unifying color-grade pass (on by default) that smooths out slight tone/tint differences between independently generated scene clips.",
)
@click.option("--bgm", default=None, help="Optional background music audio file path.")
@click.option("--enqueue", is_flag=True)
def render_cmd(
    project_ref: str,
    transition: Optional[str],
    no_burn_sub: bool,
    no_color_grade: bool,
    bgm: Optional[str],
    enqueue: bool,
) -> None:
    """Compose ready scene MP4 clips into a final H.264/AAC MP4 with FFmpeg."""
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        _trigger_project_render(
            db,
            owner.id,
            proj,
            transition=transition,
            burn_subtitles=False if no_burn_sub else None,
            color_grade=False if no_color_grade else None,
            bgm_source=bgm,
            enqueue=enqueue,
        )
    finally:
        db.close()


# --- End-to-End Flow2API Pipeline ---
@cli.command("run-pipeline")
@click.argument("project_ref")
@click.option("--max-scenes", type=int, default=3, help="Number of consecutive scenes to run (default 3).")
@click.option("--auto-approve-master", is_flag=True, help="Skip interactive Master Image approval prompt.")
@click.option("--transition", type=click.Choice(["none", "fade", "crossfade"]), default="none")
def run_pipeline(
    project_ref: str, max_scenes: int, auto_approve_master: bool, transition: str
) -> None:
    """
    Spec XVIII End-to-End Pipeline:
    1) Generate Script + 8-Layer Prompts
    2) Scene 01 Master Image -> Approve Gate
    3) Scene 02..N Continuity Images
    4) Scene 01..N Spoken Video Clips (Omni 1.2)
    5) FFmpeg Final Render MP4
    """
    db, owner = _get_db_and_owner()
    try:
        proj = _resolve_project(db, owner.id, project_ref)
        active = db.scalars(
            select(Script)
            .where(Script.project_id == proj.id, Script.is_active.is_(True))
            .order_by(Script.version.desc())
        ).first()
        if not active:
            click.echo("[1/5] Generating script & 8-layer prompts...")
            active = _generate_script_in_db(db, owner.id, proj)
        else:
            click.echo(f"[1/5] Using active Script v{active.version}: {active.title}")

        scenes = db.scalars(
            select(Scene).where(Scene.script_id == active.id).order_by(Scene.scene_index.asc())
        ).all()[: max(1, max_scenes)]
        if not scenes:
            raise click.ClickException("Script contains 0 scenes.")

        storage = MediaStorageService.from_db(db, owner_id=owner.id)

        # Step 2: Scene 01 Master Image
        s1 = scenes[0]
        if not get_active_scene_image_asset(db, s1.id):
            click.echo(f"\n[2/5] Generating Scene 01 MASTER IMAGE ({proj.image_model})...")
            job = GenerationJob(
                owner_id=owner.id,
                project_id=proj.id,
                scene_id=s1.id,
                job_type="scene_image",
                provider_model=proj.image_model,
                status="queued",
                max_attempts=3,
                idempotency_key=f"pipe-img:{s1.id}:{uuid.uuid4().hex[:8]}",
            )
            db.add(job)
            db.commit()
            done = _execute_job(db, job, run_scene_image_job, enqueue=False)
            if done.status != "completed":
                _print_job_failure(db, done)
                raise SystemExit(1)
            ma = db.get(MediaAsset, done.result_json.get("media_asset_id"))
            click.secho(f"Master Image saved: {_asset_location_hint(storage, ma)}", fg="green")

        db.refresh(s1)
        if s1.approval_status != "approved":
            if auto_approve_master or proj.auto_approve_master:
                approve_scene_image(db, s1)
                db.commit()
                click.secho("Master Image auto-approved.", fg="green")
            else:
                if not click.confirm("Approve Scene 01 Master Image and continue to Scene 02+?", default=True):
                    click.echo("Stopped before downstream continuity scenes. Inspect image and run: ./studio scene approve ...")
                    return
                approve_scene_image(db, s1)
                db.commit()

        # Step 3: Scene 02..N Continuity Images
        click.echo(f"\n[3/5] Generating continuity images for Scenes 02..{len(scenes):02d}...")
        for sc in scenes[1:]:
            if not get_active_scene_image_asset(db, sc.id):
                job = GenerationJob(
                    owner_id=owner.id,
                    project_id=proj.id,
                    scene_id=sc.id,
                    job_type="scene_image",
                    provider_model=proj.image_model,
                    status="queued",
                    max_attempts=3,
                    idempotency_key=f"pipe-img:{sc.id}:{uuid.uuid4().hex[:8]}",
                )
                db.add(job)
                db.commit()
                done = _execute_job(db, job, run_scene_image_job, enqueue=False)
                if done.status != "completed":
                    _print_job_failure(db, done)
                    raise SystemExit(1)
                ma = db.get(MediaAsset, done.result_json.get("media_asset_id"))
                click.secho(f"  Scene #{sc.scene_index:02d} image saved: {_asset_location_hint(storage, ma)}", fg="green")
            db.refresh(sc)
            if sc.approval_status != "approved":
                approve_scene_image(db, sc)
                db.commit()

        # Step 4: Scene 01..N Video Clips
        click.echo(f"\n[4/5] Generating spoken videos via {proj.video_model}...")
        for sc in scenes:
            existing_vid = db.scalars(
                select(SceneAsset).where(
                    SceneAsset.scene_id == sc.id,
                    SceneAsset.asset_role == "scene_video",
                    SceneAsset.is_active.is_(True),
                )
            ).first()
            if existing_vid:
                continue
            job = GenerationJob(
                owner_id=owner.id,
                project_id=proj.id,
                scene_id=sc.id,
                job_type="scene_video",
                provider_model=proj.video_model,
                status="queued",
                max_attempts=3,
                idempotency_key=f"pipe-vid:{sc.id}:{uuid.uuid4().hex[:8]}",
            )
            db.add(job)
            db.commit()
            click.echo(f"  Generating Scene #{sc.scene_index:02d} video...")
            done = _execute_job(db, job, run_scene_video_job, enqueue=False)
            if done.status != "completed":
                _print_job_failure(db, done)
                raise SystemExit(1)
            ma = db.get(MediaAsset, done.result_json.get("media_asset_id"))
            click.secho(
                f"  Scene #{sc.scene_index:02d} video ready ({done.result_json.get('actual_duration_sec')}s): "
                f"{_asset_location_hint(storage, ma)}",
                fg="green",
            )

        # Step 5: FFmpeg Composition
        click.echo("\n[5/5] Compositing final MP4 via FFmpeg...")
        _trigger_project_render(db, owner.id, proj, transition=transition, enqueue=False)
    finally:
        db.close()


# --- Zero-Key Local Demo ---
@cli.command("demo-local")
@click.option("--output", default=None, help="Optional destination path to copy the final demo MP4.")
def demo_local(output: Optional[str]) -> None:
    """
    Run a complete 3-scene local E2E demo WITHOUT calling paid Flow2API:
    Creates synthetic Character/Background PNGs, generates script + 8-layer prompts,
    creates 3 synthetic MP4 clips, stitches with FFmpeg + SRT subtitles, and outputs final MP4.
    """
    db, owner = _get_db_and_owner()
    try:
        storage = MediaStorageService.from_db(db, owner_id=owner.id)
        tag = uuid.uuid4().hex[:6]

        def _make_png(label: str, bg_rgb: tuple[int, int, int]) -> bytes:
            img = Image.new("RGB", (720, 1280), bg_rgb)
            draw = ImageDraw.Draw(img)
            draw.rectangle([60, 120, 660, 1160], outline=(240, 240, 245), width=4)
            draw.text((100, 580), label, fill=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        char_asset = storage.save_bytes(
            db,
            owner_id=owner.id,
            data=_make_png("Senior Forex Expert (Ref)", (30, 41, 59)),
            original_filename=f"demo_char_{tag}.png",
            mime_type="image/png",
            asset_kind="image",
        )
        bg_asset = storage.save_bytes(
            db,
            owner_id=owner.id,
            data=_make_png("Trading Office (Ref)", (15, 23, 42)),
            original_filename=f"demo_bg_{tag}.png",
            mime_type="image/png",
            asset_kind="image",
        )

        char = Character(
            owner_id=owner.id,
            name=f"Senior Forex Expert ({tag})",
            age_appearance="60",
            voice_style="Mature American male",
            role_title="Presenter",
            primary_image_asset_id=char_asset.id,
        )
        bg = Background(
            owner_id=owner.id,
            name=f"Modern Trading Office ({tag})",
            style="photorealistic",
            media_asset_id=bg_asset.id,
        )
        db.add_all([char, bg])
        db.flush()
        db.add(CharacterRule(owner_id=owner.id, character_id=char.id))

        proj = Project(
            owner_id=owner.id,
            name=f"Demo Local XAUUSD {tag}",
            topic="XAUUSD Smart Money Concepts",
            topic_category="forex",
            language="en-US",
            target_duration_seconds=24,
            aspect_ratio="9:16",
            default_character_id=char.id,
            default_background_id=bg.id,
            auto_approve_master=True,
        )
        db.add(proj)
        db.commit()

        click.secho(f"[1/4] Project created: {proj.id} ({proj.name})", fg="cyan")
        script_row = _generate_script_in_db(db, owner.id, proj)
        scenes = db.scalars(
            select(Scene).where(Scene.script_id == script_row.id).order_by(Scene.scene_index.asc())
        ).all()
        click.echo(f"[2/4] Generated Script v{script_row.version} with {len(scenes)} scenes + 8-layer prompts.")

        # Synthesize 3 local portrait MP4 clips of varying durations to verify timeline duration sync + FFmpeg
        colors_freqs = [("navy", 440, 2.5), ("teal", 523, 3.0), ("indigo", 659, 2.0)]
        with TempWorkspaceManager(prefix="cli_demo") as tmp_dir:
            for sc, (color, freq, dur) in zip(scenes[:3], colors_freqs):
                clip_path = tmp_dir / f"scene_{sc.scene_index:02d}.mp4"
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c={color}:s=720x1280:d={dur}:r=30",
                        "-f",
                        "lavfi",
                        "-i",
                        f"sine=frequency={freq}:sample_rate=44100:duration={dur}",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "ultrafast",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-shortest",
                        str(clip_path),
                    ],
                    check=True,
                    timeout=30,
                )
                vid_asset = storage.save_file(
                    db,
                    owner_id=owner.id,
                    file_path=clip_path,
                    original_filename=clip_path.name,
                    mime_type="video/mp4",
                    asset_kind="video",
                    project_id=proj.id,
                )
                db.add(
                    SceneAsset(
                        scene_id=sc.id,
                        media_asset_id=vid_asset.id,
                        asset_role="scene_video",
                        version=1,
                        is_active=True,
                    )
                )
                sc.actual_duration_sec = vid_asset.duration_seconds or dur
                sc.status = "video_ready"
        db.commit()
        click.echo("[3/4] Attached 3 synthetic portrait MP4 clips to Scenes 01–03.")

        click.echo("[4/4] Running FFmpeg Composer with SRT subtitle burn-in...")
        render_row = _trigger_project_render(
            db, owner.id, proj, transition="crossfade", burn_subtitles=True, enqueue=False
        )
        if output and render_row.output_media_asset_id:
            mp4 = db.get(MediaAsset, render_row.output_media_asset_id)
            if mp4 and mp4.storage_backend == "local":
                dest = Path(output).expanduser().resolve()
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(storage.local.resolve_path(mp4.storage_key), dest)
                click.secho(f"Copied final demo video to: {dest}", fg="green")
    finally:
        db.close()


# --- Server / Worker shortcuts ---
@cli.command()
@click.option("--host", default="127.0.0.1")
@click.option("--port", type=int, default=None, help="Port to bind (defaults to APP_PORT or 8010).")
@click.option("--reload", is_flag=True)
def serve(host: str, port: Optional[int], reload: bool) -> None:
    """Start the FastAPI backend server."""
    chosen_port = port or get_settings().app_port or 8010
    uvicorn.run("app.main:app", host=host, port=chosen_port, reload=reload)


@cli.command()
@click.option("--processes", type=int, default=1)
@click.option("--threads", type=int, default=2)
def worker(processes: int, threads: int) -> None:
    """Start the Dramatiq background job worker."""
    import subprocess as sp

    sp.run(
        [
            sys.executable,
            "-m",
            "dramatiq",
            "app.workers.tasks",
            "--processes",
            str(processes),
            "--threads",
            str(threads),
        ],
        check=True,
    )


if __name__ == "__main__":
    cli()

