import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import normalize_v1_base_url
from app.services.flow2api.parser import (
    Flow2APIUpstreamError,
    classify_model_entry,
    extract_error_code,
    parse_image_url_from_content,
    parse_video_from_content,
)

logger = logging.getLogger(__name__)


class Flow2APIClient:
    """HTTPX client implementing Flow2API endpoints per https://flow.aetrading.vn/huongdan."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        image_timeout: int = 180,
        video_timeout: int = 900,
    ):
        self.base_url = normalize_v1_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.image_timeout = image_timeout
        self.video_timeout = video_timeout

    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise Flow2APIUpstreamError(
                "FLOW2API_API_KEY is not configured. Add it in Settings or .env before calling Flow2API.",
                status_code=401,
                error_code="MISSING_API_KEY",
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _extract_http_error(resp: httpx.Response) -> Flow2APIUpstreamError:
        body_text = resp.text[:1200]
        msg = body_text
        try:
            data = resp.json()
            if isinstance(data, dict):
                err = data.get("error")
                if isinstance(err, dict):
                    msg = str(err.get("message") or body_text)
                elif isinstance(data.get("detail"), str):
                    msg = data["detail"]
        except Exception:
            pass
        return Flow2APIUpstreamError(
            msg,
            status_code=resp.status_code,
            error_code=extract_error_code(msg),
            response_excerpt=body_text,
        )

    def list_models(self) -> list[dict[str, Any]]:
        """GET /v1/models and classify image/video models by type and output_modalities."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(f"{self.base_url}/models", headers=self._headers())
        if resp.status_code >= 400:
            raise self._extract_http_error(resp)
        data = resp.json().get("data") or []
        return [classify_model_entry(item) for item in data if isinstance(item, dict)]

    def test_connection(self) -> dict[str, Any]:
        """Verify API key against GET /v1/models and return categorized model counts."""
        models = self.list_models()
        images = [m for m in models if m["type"] == "image"]
        videos = [m for m in models if m["type"] == "video"]
        return {
            "ok": True,
            "total_models": len(models),
            "image_models": len(images),
            "video_models": len(videos),
            "sample_models": [m["id"] for m in (images[:3] + videos[:3])],
        }

    @staticmethod
    def _build_content(prompt: str, references: list[str]) -> str | list[dict[str, Any]]:
        if not references:
            return prompt
        blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for ref in references:
            if ref:
                blocks.append({"type": "image_url", "image_url": {"url": ref}})
        return blocks

    def _call_chat_completions(
        self,
        *,
        model: str,
        prompt: str,
        references: list[str],
        timeout_seconds: int,
        stream: bool = True,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": self._build_content(prompt, references)}],
            "stream": stream,
        }
        headers = self._headers()

        if not stream:
            try:
                with httpx.Client(timeout=float(timeout_seconds)) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions", headers=headers, json=payload
                    )
            except httpx.TimeoutException as exc:
                raise Flow2APIUpstreamError(
                    f"Request timed out after {timeout_seconds}s: {exc}",
                    status_code=504,
                    error_code="NETWORK_TIMEOUT",
                ) from exc
            if resp.status_code >= 400:
                raise self._extract_http_error(resp)
            body = resp.json()
            return body["choices"][0]["message"]["content"]

        # Streaming SSE handler
        content_chunks: list[str] = []
        progress_step = 15
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout_seconds, connect=30.0)) as client:
                with client.stream(
                    "POST", f"{self.base_url}/chat/completions", headers=headers, json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        resp.read()
                        raise self._extract_http_error(resp)

                    for raw_line in resp.iter_lines():
                        if not raw_line:
                            continue
                        line = raw_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data_part = line[5:].strip()
                        if data_part == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_part)
                        except json.JSONDecodeError:
                            continue

                        if "error" in chunk and isinstance(chunk["error"], dict):
                            err_msg = str(chunk["error"].get("message") or data_part)
                            raise Flow2APIUpstreamError(
                                err_msg,
                                status_code=chunk["error"].get("status_code"),
                                error_code=extract_error_code(err_msg),
                            )

                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning and on_progress:
                            progress_step = min(90, progress_step + 8)
                            on_progress(str(reasoning).strip()[:200], progress_step)
                        piece = delta.get("content")
                        if piece:
                            content_chunks.append(str(piece))
        except httpx.TimeoutException as exc:
            raise Flow2APIUpstreamError(
                f"Streaming generation timed out after {timeout_seconds}s: {exc}",
                status_code=504,
                error_code="NETWORK_TIMEOUT",
            ) from exc

        return "".join(content_chunks)

    def generate_image(
        self,
        *,
        model: str,
        prompt: str,
        references: Optional[list[str]] = None,
        stream: bool = True,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> dict[str, str]:
        content = self._call_chat_completions(
            model=model,
            prompt=prompt,
            references=references or [],
            timeout_seconds=self.image_timeout,
            stream=stream,
            on_progress=on_progress,
        )
        url = parse_image_url_from_content(content)
        return {"url": url, "raw_content": content}

    def generate_video(
        self,
        *,
        model: str,
        prompt: str,
        references: Optional[list[str]] = None,
        stream: bool = True,
        on_progress: Optional[Callable[[str, int], None]] = None,
    ) -> dict[str, Optional[str]]:
        content = self._call_chat_completions(
            model=model,
            prompt=prompt,
            references=references or [],
            timeout_seconds=self.video_timeout,
            stream=stream,
            on_progress=on_progress,
        )
        url, media_id = parse_video_from_content(content)
        return {"url": url, "media_id": media_id, "raw_content": content}

    def download_temporary_asset(self, url: str, dest_path: Path, timeout: float = 180.0) -> Path:
        """Immediately download generated temporary Flow2API file to local scratch file."""
        target_host = (urlparse(url).hostname or "").lower()
        flow_host = (urlparse(self.base_url).hostname or "").lower()
        headers = {}
        if self.api_key and target_host and target_host == flow_host:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
                resp.raise_for_status()
                with dest_path.open("wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=1024 * 256):
                        fh.write(chunk)
        return dest_path

