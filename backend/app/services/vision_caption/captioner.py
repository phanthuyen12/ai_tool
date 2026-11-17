import base64
import io
import logging
from typing import Optional

import httpx
from PIL import Image, ImageOps

from app.services.provider_config import EffectiveRuntimeConfig

logger = logging.getLogger(__name__)

MAX_CAPTION_SIDE = 1024


def _encode_data_uri(image_bytes: bytes) -> str:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        if max(img.width, img.height) > MAX_CAPTION_SIDE:
            img.thumbnail((MAX_CAPTION_SIDE, MAX_CAPTION_SIDE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def generate_image_caption(
    image_bytes: bytes,
    runtime_cfg: EffectiveRuntimeConfig,
    *,
    subject_hint: str,
) -> Optional[str]:
    """Auto-describe a reference image using a configured vision-capable LLM.

    Open-weight models (Qwen2-VL, LLaVA, InternVL, ...) served behind an
    OpenAI-compatible endpoint (vLLM, Ollama, etc.) work here -- same provider
    pattern as script_llm. Returns None when disabled/unconfigured or on any
    upstream failure; callers keep the existing/blank description in that case.
    """
    if not runtime_cfg.caption_llm_enabled:
        return None
    base_url = (runtime_cfg.caption_llm_base_url or "").strip().rstrip("/")
    model = (runtime_cfg.caption_llm_model or "").strip()
    if not base_url or not model:
        return None

    try:
        data_uri = _encode_data_uri(image_bytes)
    except Exception as exc:
        logger.warning("Caption LLM: failed to prepare image: %s", exc)
        return None

    prompt = (
        f"Describe this {subject_hint} reference image in 1-2 concise sentences for a video "
        "generation prompt: physical appearance, clothing, pose/expression, and notable "
        "environment details. Be factual and specific. Do not mention that it is an image "
        "or photo. Output plain text only, no markdown."
    )

    try:
        provider = (runtime_cfg.caption_llm_provider or "openai_compatible").lower()
        if provider == "gemini":
            url = f"{base_url}/models/{model}:generateContent"
            mime, b64 = data_uri.split(";base64,", 1)
            mime = mime.replace("data:", "")
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    url,
                    headers={
                        "x-goog-api-key": runtime_cfg.caption_llm_api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": prompt},
                                    {"inline_data": {"mime_type": mime, "data": b64}},
                                ],
                            }
                        ],
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            url = f"{base_url}/chat/completions"
            headers = {"Content-Type": "application/json"}
            if runtime_cfg.caption_llm_api_key:
                headers["Authorization"] = f"Bearer {runtime_cfg.caption_llm_api_key}"
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    url,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": data_uri}},
                                ],
                            }
                        ],
                        "temperature": 0.4,
                        "max_tokens": 200,
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]

        cleaned = " ".join(str(text).strip().split())
        return cleaned[:500] if cleaned else None
    except Exception as exc:
        logger.warning("Caption LLM request failed, keeping existing description: %s", exc)
        return None
