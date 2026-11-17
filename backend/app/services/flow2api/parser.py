import re
from dataclasses import dataclass
from typing import Any, Optional

IMAGE_MD_PATTERN = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")
VIDEO_SRC_PATTERN = re.compile(r"<video[^>]*\ssrc=['\"](https?://[^'\"]+)['\"]", re.IGNORECASE)
VIDEO_MEDIA_ID_PATTERN = re.compile(r"data-media-id=['\"]([^'\"]+)['\"]", re.IGNORECASE)
VIDEO_DIRECT_URL_PATTERN = re.compile(
    r"(https?://[^\s'\"<>]+\.(?:mp4|webm|mov)(?:\?[^\s'\"<>]*)?)", re.IGNORECASE
)
PUBLIC_ERROR_PATTERN = re.compile(r"(PUBLIC_ERROR_[A-Z_]+)")

DEFAULT_FLOW2API_CATALOG: list[dict[str, Any]] = [
    {
        "id": "gemini-3.1-flash-image-portrait-2k",
        "type": "image",
        "output_modalities": ["image"],
        "description": "Nano Banana 2.1 Portrait 2K (9:16)",
        "source": "catalog_default",
    },
    {
        "id": "gemini-3.1-flash-image-landscape-2k",
        "type": "image",
        "output_modalities": ["image"],
        "description": "Nano Banana 2.1 Landscape 2K (16:9)",
        "source": "catalog_default",
    },
    {
        "id": "gemini-3.1-flash-image-square-2k",
        "type": "image",
        "output_modalities": ["image"],
        "description": "Nano Banana 2.1 Square 2K (1:1)",
        "source": "catalog_default",
    },
    {
        "id": "gemini-3.1-flash-lite-image-portrait-2k",
        "type": "image",
        "output_modalities": ["image"],
        "description": "Nano Banana 2 Lite Fast Portrait 2K",
        "source": "catalog_default",
    },
    {
        "id": "omni-1.2-flash-8s-portrait",
        "type": "video",
        "output_modalities": ["video"],
        "description": "Omni 1.2 Flash 8s Portrait (T2V/R2V with dialogue)",
        "source": "catalog_default",
    },
    {
        "id": "omni-1.2-flash-8s-landscape",
        "type": "video",
        "output_modalities": ["video"],
        "description": "Omni 1.2 Flash 8s Landscape (T2V/R2V with dialogue)",
        "source": "catalog_default",
    },
    {
        "id": "omni-1.2-flash-15s-portrait",
        "type": "video",
        "output_modalities": ["video"],
        "description": "Omni 1.2 Flash 15s Portrait",
        "source": "catalog_default",
    },
    {
        "id": "veo-3.1-fast-i2v-6s-portrait",
        "type": "video",
        "output_modalities": ["video"],
        "description": "Veo 3.1 Fast I2V 6s Portrait",
        "source": "catalog_default",
    },
    {
        "id": "veo-3.1-fast-r2v-8s-portrait",
        "type": "video",
        "output_modalities": ["video"],
        "description": "Veo 3.1 Fast R2V 8s Portrait",
        "source": "catalog_default",
    },
]


class Flow2APIUpstreamError(Exception):
    """Error raised when Flow2API returns HTTP failure, upstream rejection, or malformed media output."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: Optional[str] = None,
        response_excerpt: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code or extract_error_code(message)
        self.response_excerpt = response_excerpt or message[:800]


@dataclass
class ErrorDecision:
    error_code: str
    retryable: bool
    max_retries: int
    backoff_seconds: int
    adjust_audio_prompt: bool
    user_message: str


def classify_model_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Classify a /v1/models item using type and output_modalities per Spec VIII.2."""
    model_type = str(raw.get("type") or "").lower().strip()
    modalities = [str(m).lower().strip() for m in (raw.get("output_modalities") or [])]
    if not model_type:
        if "video" in modalities:
            model_type = "video"
        elif "image" in modalities:
            model_type = "image"
        else:
            mid = str(raw.get("id") or "").lower()
            if any(k in mid for k in ("omni", "veo")):
                model_type = "video"
            elif "image" in mid:
                model_type = "image"
            else:
                model_type = "other"
    return {
        "id": raw.get("id", ""),
        "type": model_type,
        "output_modalities": modalities or ([model_type] if model_type in ("image", "video") else []),
        "description": raw.get("description") or raw.get("id", ""),
        "source": raw.get("source", "flow2api_live"),
    }


def extract_error_code(message: str) -> Optional[str]:
    """Extract canonical Flow2API / Google Flow error code from raw error message."""
    if not message:
        return None
    m = PUBLIC_ERROR_PATTERN.search(message)
    if m:
        return m.group(1)
    if "Failed to obtain reCAPTCHA token" in message or "reCAPTCHA token" in message:
        return "RECAPTCHA_TOKEN_FAILED"
    return None


def classify_flow2api_error(
    *, status_code: Optional[int], message: str
) -> ErrorDecision:
    """Apply Spec VIII.5 & Flow2API documentation retry/stop policy."""
    code = extract_error_code(message)

    if code == "PUBLIC_ERROR_AUDIO_FILTERED":
        return ErrorDecision(
            error_code=code,
            retryable=True,
            max_retries=2,
            backoff_seconds=3,
            adjust_audio_prompt=True,
            user_message="Upstream audio safety filter blocked clip generation (PUBLIC_ERROR_AUDIO_FILTERED).",
        )
    if code == "PUBLIC_ERROR_UNSAFE_GENERATION":
        return ErrorDecision(
            error_code=code,
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message="Prompt or reference image rejected by safety filter (PUBLIC_ERROR_UNSAFE_GENERATION). Please edit prompt/reference.",
        )
    if code == "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED":
        return ErrorDecision(
            error_code=code,
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message="Reference blocked by prominent-people policy (PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED). Replace reference image.",
        )
    if code == "PUBLIC_ERROR_MODEL_ACCESS_DENIED":
        return ErrorDecision(
            error_code=code,
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message="Account lacks access to selected Flow2API model (PUBLIC_ERROR_MODEL_ACCESS_DENIED).",
        )
    if code == "PUBLIC_ERROR_USER_QUOTA_REACHED":
        return ErrorDecision(
            error_code=code,
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message="Flow2API upstream quota/credits exhausted (PUBLIC_ERROR_USER_QUOTA_REACHED).",
        )
    if code == "PUBLIC_ERROR_UNUSUAL_ACTIVITY":
        return ErrorDecision(
            error_code=code,
            retryable=True,
            max_retries=1,
            backoff_seconds=30,
            adjust_audio_prompt=False,
            user_message="Upstream reported unusual activity (PUBLIC_ERROR_UNUSUAL_ACTIVITY).",
        )
    if code == "RECAPTCHA_TOKEN_FAILED":
        return ErrorDecision(
            error_code=code,
            retryable=True,
            max_retries=2,
            backoff_seconds=30,
            adjust_audio_prompt=False,
            user_message="Flow2API failed to obtain upstream reCAPTCHA token; retrying with backoff.",
        )

    if status_code == 400:
        return ErrorDecision(
            error_code="HTTP_400",
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message=f"Invalid Flow2API request (HTTP 400): {message}",
        )
    if status_code == 401:
        return ErrorDecision(
            error_code="HTTP_401",
            retryable=False,
            max_retries=0,
            backoff_seconds=0,
            adjust_audio_prompt=False,
            user_message="Flow2API authentication failed (HTTP 401). Check FLOW2API_API_KEY in Settings.",
        )
    if status_code == 429:
        return ErrorDecision(
            error_code="HTTP_429",
            retryable=True,
            max_retries=2,
            backoff_seconds=15,
            adjust_audio_prompt=False,
            user_message="Flow2API rate limit reached (HTTP 429). Retrying with exponential backoff.",
        )
    if status_code in (502, 503, 504):
        return ErrorDecision(
            error_code=f"HTTP_{status_code}",
            retryable=True,
            max_retries=2,
            backoff_seconds=15,
            adjust_audio_prompt=False,
            user_message=f"Flow2API upstream gateway/timeout error (HTTP {status_code}).",
        )
    if status_code == 408 or "timeout" in message.lower():
        return ErrorDecision(
            error_code="NETWORK_TIMEOUT",
            retryable=True,
            max_retries=1,
            backoff_seconds=10,
            adjust_audio_prompt=False,
            user_message=f"Flow2API request timed out: {message}",
        )

    return ErrorDecision(
        error_code=code or (f"HTTP_{status_code}" if status_code else "UPSTREAM_ERROR"),
        retryable=False,
        max_retries=0,
        backoff_seconds=0,
        adjust_audio_prompt=False,
        user_message=message or "Unknown Flow2API upstream error",
    )


def _check_stream_error_text(content: str) -> None:
    stripped = content.strip()
    if stripped.startswith(("错误:", "错误：", "Error:", "error:")):
        raise Flow2APIUpstreamError(stripped, error_code=extract_error_code(stripped))
    if "PUBLIC_ERROR_" in stripped or "Failed to obtain reCAPTCHA token" in stripped:
        # Only raise if there isn't a valid URL embedded
        if not IMAGE_MD_PATTERN.search(stripped) and not VIDEO_SRC_PATTERN.search(stripped):
            raise Flow2APIUpstreamError(stripped, error_code=extract_error_code(stripped))


def parse_image_url_from_content(content: str) -> str:
    """Parse Markdown image URL ![Generated Image](URL) from Flow2API response content."""
    if not content:
        raise Flow2APIUpstreamError("Empty response content from Flow2API image endpoint")
    _check_stream_error_text(content)
    match = IMAGE_MD_PATTERN.search(content)
    if match:
        return match.group(1).strip()
    raise Flow2APIUpstreamError(
        f"Could not extract Markdown image URL from Flow2API response: {content[:240]}",
        response_excerpt=content[:500],
    )


def parse_video_from_content(content: str) -> tuple[str, Optional[str]]:
    """
    Parse <video src='...' data-media-id='...'> (single or double quotes) or direct .mp4 URL
    from Flow2API video completion content.
    """
    if not content:
        raise Flow2APIUpstreamError("Empty response content from Flow2API video endpoint")
    _check_stream_error_text(content)

    media_match = VIDEO_MEDIA_ID_PATTERN.search(content)
    media_id = media_match.group(1).strip() if media_match else None

    src_match = VIDEO_SRC_PATTERN.search(content)
    if src_match:
        return src_match.group(1).strip(), media_id

    direct_match = VIDEO_DIRECT_URL_PATTERN.search(content)
    if direct_match:
        return direct_match.group(1).strip(), media_id

    raise Flow2APIUpstreamError(
        f"Could not extract video URL from Flow2API response: {content[:240]}",
        response_excerpt=content[:500],
    )

