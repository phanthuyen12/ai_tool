import boto3
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import normalize_v1_base_url
from app.core.database import get_db
from app.core.security import encrypt_secret, get_current_user, mask_secret
from app.models.entities import ProviderSetting, User
from app.schemas.studio import (
    ProviderSettingResponse,
    ProviderSettingUpdateRequest,
    ProviderTestRequest,
)
from app.services.flow2api.client import Flow2APIClient
from app.services.flow2api.parser import DEFAULT_FLOW2API_CATALOG, Flow2APIUpstreamError
from app.services.provider_config import get_effective_runtime_config

router = APIRouter(prefix="/providers", tags=["providers"])

PROVIDER_KEYS = ["flow2api", "script_llm", "cloudinary", "r2", "worker"]


@router.get("/settings", response_model=list[ProviderSettingResponse])
def list_provider_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    rows = db.scalars(
        select(ProviderSetting).where(ProviderSetting.owner_id == current_user.id)
    ).all()
    by_key = {r.provider_key: r for r in rows}

    env_defaults = {
        "flow2api": (
            runtime.flow2api_base_url,
            runtime.flow2api_api_key,
            None,
            {
                "image_timeout": runtime.flow2api_image_timeout,
                "video_timeout": runtime.flow2api_video_timeout,
            },
        ),
        "script_llm": (
            runtime.script_llm_base_url,
            runtime.script_llm_api_key,
            runtime.script_llm_model,
            {"provider": runtime.script_llm_provider},
        ),
        "cloudinary": (
            None,
            runtime.cloudinary_api_secret,
            None,
            {
                "enabled": runtime.storage_image_backend == "cloudinary",
                "cloud_name": runtime.cloudinary_cloud_name,
                "api_key": runtime.cloudinary_api_key,
                "folder": runtime.cloudinary_folder,
            },
        ),
        "r2": (
            runtime.r2_endpoint_url,
            runtime.r2_secret_access_key,
            None,
            {
                "enabled": runtime.storage_video_backend == "r2",
                "access_key_id": runtime.r2_access_key_id,
                "bucket_name": runtime.r2_bucket_name,
                "public_base_url": runtime.r2_public_base_url,
            },
        ),
        "worker": (
            None,
            "",
            None,
            {
                "video_concurrency": runtime.worker_video_concurrency,
                "image_concurrency": runtime.worker_image_concurrency,
                "request_delay_seconds": runtime.worker_request_delay_seconds,
                "default_language": runtime.default_language,
                "default_voice_mode": runtime.default_voice_mode,
                "default_voice_description": runtime.default_voice_description,
            },
        ),
    }

    out: list[ProviderSettingResponse] = []
    for key in PROVIDER_KEYS:
        row = by_key.get(key)
        def_url, def_secret, def_model, def_cfg = env_defaults[key]
        out.append(
            ProviderSettingResponse(
                provider_key=key,
                base_url=row.base_url if (row and row.base_url) else def_url,
                has_api_key=bool(def_secret),
                api_key_masked=mask_secret(def_secret),
                model_default=row.model_default if (row and row.model_default) else def_model,
                config_json={**def_cfg, **(row.config_json if row else {})},
                is_active=row.is_active if row else True,
                updated_at=row.updated_at if row else None,
            )
        )
    return out


@router.put("/settings/{provider_key}", response_model=ProviderSettingResponse)
def upsert_provider_setting(
    provider_key: str,
    payload: ProviderSettingUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if provider_key not in PROVIDER_KEYS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider_key: {provider_key}")

    row = db.scalars(
        select(ProviderSetting).where(
            ProviderSetting.owner_id == current_user.id,
            ProviderSetting.provider_key == provider_key,
        )
    ).first()
    if not row:
        row = ProviderSetting(owner_id=current_user.id, provider_key=provider_key)
        db.add(row)

    if payload.base_url is not None:
        cleaned_url = payload.base_url.strip()
        if provider_key == "flow2api" and cleaned_url:
            cleaned_url = normalize_v1_base_url(cleaned_url)
        row.base_url = cleaned_url or None
    if payload.api_key and payload.api_key.strip():
        row.api_key_encrypted = encrypt_secret(payload.api_key.strip())
    if payload.model_default is not None:
        row.model_default = payload.model_default.strip() or None

    merged_cfg = dict(row.config_json or {})
    merged_cfg.update(payload.config_json or {})
    row.config_json = merged_cfg
    row.is_active = payload.is_active

    db.commit()
    db.refresh(row)

    all_settings = list_provider_settings(current_user=current_user, db=db)
    return next(s for s in all_settings if s.provider_key == provider_key)


@router.get("/models")
def get_available_models(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    if runtime.flow2api_cached_models:
        return {
            "source": "flow2api_cached",
            "has_api_key": bool(runtime.flow2api_api_key),
            "models": runtime.flow2api_cached_models,
        }

    if runtime.flow2api_api_key:
        try:
            client = Flow2APIClient(
                base_url=runtime.flow2api_base_url,
                api_key=runtime.flow2api_api_key,
                image_timeout=runtime.flow2api_image_timeout,
                video_timeout=runtime.flow2api_video_timeout,
            )
            live_models = client.list_models()
            _cache_flow2api_models(db, current_user.id, live_models)
            return {
                "source": "flow2api_live",
                "has_api_key": True,
                "models": live_models,
            }
        except Flow2APIUpstreamError:
            pass

    return {
        "source": "catalog_default",
        "has_api_key": bool(runtime.flow2api_api_key),
        "models": DEFAULT_FLOW2API_CATALOG,
    }


def _cache_flow2api_models(db: Session, owner_id: str, models: list[dict]) -> None:
    row = db.scalars(
        select(ProviderSetting).where(
            ProviderSetting.owner_id == owner_id, ProviderSetting.provider_key == "flow2api"
        )
    ).first()
    if not row:
        row = ProviderSetting(owner_id=owner_id, provider_key="flow2api", config_json={})
        db.add(row)
    cfg = dict(row.config_json or {})
    cfg["cached_models"] = models
    row.config_json = cfg
    db.commit()


@router.post("/models/refresh")
def refresh_flow2api_models(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    client = Flow2APIClient(
        base_url=runtime.flow2api_base_url,
        api_key=runtime.flow2api_api_key,
        image_timeout=runtime.flow2api_image_timeout,
        video_timeout=runtime.flow2api_video_timeout,
    )
    try:
        live_models = client.list_models()
    except Flow2APIUpstreamError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc)) from exc

    _cache_flow2api_models(db, current_user.id, live_models)
    return {
        "source": "flow2api_live",
        "total": len(live_models),
        "models": live_models,
    }


@router.post("/test")
def test_provider_connection(
    payload: ProviderTestRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    runtime = get_effective_runtime_config(db, owner_id=current_user.id)
    key = payload.provider_key

    if key == "flow2api":
        client = Flow2APIClient(
            base_url=runtime.flow2api_base_url,
            api_key=runtime.flow2api_api_key,
            image_timeout=runtime.flow2api_image_timeout,
            video_timeout=runtime.flow2api_video_timeout,
        )
        try:
            res = client.test_connection()
            return {"provider_key": key, **res}
        except Flow2APIUpstreamError as exc:
            return {
                "provider_key": key,
                "ok": False,
                "error_code": exc.error_code,
                "message": str(exc),
            }

    if key == "script_llm":
        if not runtime.script_llm_api_key:
            return {
                "provider_key": key,
                "ok": False,
                "message": "SCRIPT_LLM_API_KEY is empty. Template fallback mode is active until configured.",
            }
        try:
            with httpx.Client(timeout=20.0) as client:
                if runtime.script_llm_provider == "gemini":
                    url = f"{runtime.script_llm_base_url.rstrip('/')}/models/{runtime.script_llm_model}:generateContent"
                    r = client.post(
                        url,
                        headers={"x-goog-api-key": runtime.script_llm_api_key},
                        json={"contents": [{"role": "user", "parts": [{"text": "Reply OK"}]}]},
                    )
                else:
                    url = f"{runtime.script_llm_base_url.rstrip('/')}/chat/completions"
                    r = client.post(
                        url,
                        headers={"Authorization": f"Bearer {runtime.script_llm_api_key}"},
                        json={
                            "model": runtime.script_llm_model,
                            "messages": [{"role": "user", "content": "Reply OK"}],
                            "max_tokens": 5,
                        },
                    )
            return {"provider_key": key, "ok": r.status_code < 400, "status_code": r.status_code}
        except Exception as exc:
            return {"provider_key": key, "ok": False, "message": str(exc)}

    if key == "cloudinary":
        ok = bool(
            runtime.cloudinary_cloud_name and runtime.cloudinary_api_key and runtime.cloudinary_api_secret
        )
        if not ok:
            return {"provider_key": key, "ok": False, "message": "Missing Cloudinary credentials"}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(
                    f"https://api.cloudinary.com/v1_1/{runtime.cloudinary_cloud_name}/resources/image",
                    auth=(runtime.cloudinary_api_key, runtime.cloudinary_api_secret),
                    params={"max_results": 1},
                )
            return {"provider_key": key, "ok": r.status_code < 400, "status_code": r.status_code}
        except Exception as exc:
            return {"provider_key": key, "ok": False, "message": str(exc)}

    if key == "r2":
        if not (runtime.r2_endpoint_url and runtime.r2_access_key_id and runtime.r2_secret_access_key):
            return {"provider_key": key, "ok": False, "message": "Missing Cloudflare R2 credentials"}
        try:
            s3 = boto3.client(
                "s3",
                endpoint_url=runtime.r2_endpoint_url,
                aws_access_key_id=runtime.r2_access_key_id,
                aws_secret_access_key=runtime.r2_secret_access_key,
                region_name="auto",
            )
            s3.head_bucket(Bucket=runtime.r2_bucket_name)
            return {"provider_key": key, "ok": True, "bucket": runtime.r2_bucket_name}
        except Exception as exc:
            return {"provider_key": key, "ok": False, "message": str(exc)}

    return {"provider_key": key, "ok": False, "message": "Unknown provider"}
