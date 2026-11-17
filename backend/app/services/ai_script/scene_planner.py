import math
import re
from dataclasses import dataclass
from typing import Any, Optional


MODEL_DURATION_PATTERN = re.compile(r"-(\d{1,2})s(?:-|$)", re.IGNORECASE)


@dataclass
class SceneWindow:
    scene_index: int
    start_time_sec: float
    end_time_sec: float
    duration_sec: float
    character_id: Optional[str]
    background_id: Optional[str]
    is_master_scene: bool


def parse_model_clip_duration(video_model: str, default: int = 8) -> int:
    """Extract per-clip duration limit in seconds from Flow2API model name."""
    if not video_model:
        return default
    match = MODEL_DURATION_PATTERN.search(video_model)
    if match:
        val = int(match.group(1))
        if 2 <= val <= 30:
            return val
    return default


def plan_scene_windows(
    target_duration_sec: int,
    clip_duration_sec: int,
    character_schedule: Optional[list[dict[str, Any]]] = None,
    default_character_id: Optional[str] = None,
    default_background_id: Optional[str] = None,
) -> list[SceneWindow]:
    """
    Split target duration and character appearance schedule into model-compatible scenes.
    Never puts 60s into a single request; splits into <= clip_duration_sec chunks and handles remainders.
    """
    total = max(4, int(target_duration_sec))
    max_clip = max(2, min(15, int(clip_duration_sec)))

    raw_intervals: list[dict[str, Any]] = []
    if character_schedule:
        sorted_sched = sorted(character_schedule, key=lambda x: float(x.get("start_sec", 0)))
        cursor = 0.0
        for item in sorted_sched:
            s = max(cursor, float(item.get("start_sec", cursor)))
            e = min(float(total), float(item.get("end_sec", total)))
            if e <= s:
                continue
            if s > cursor + 0.01:
                raw_intervals.append(
                    {
                        "start_sec": cursor,
                        "end_sec": s,
                        "character_id": default_character_id,
                        "background_id": default_background_id,
                    }
                )
            raw_intervals.append(
                {
                    "start_sec": s,
                    "end_sec": e,
                    "character_id": item.get("character_id") or default_character_id,
                    "background_id": item.get("background_id") or default_background_id,
                }
            )
            cursor = e
        if cursor < total - 0.01:
            raw_intervals.append(
                {
                    "start_sec": cursor,
                    "end_sec": float(total),
                    "character_id": default_character_id,
                    "background_id": default_background_id,
                }
            )
    else:
        raw_intervals.append(
            {
                "start_sec": 0.0,
                "end_sec": float(total),
                "character_id": default_character_id,
                "background_id": default_background_id,
            }
        )

    windows: list[SceneWindow] = []
    idx = 1
    for interval in raw_intervals:
        cur = float(interval["start_sec"])
        end = float(interval["end_sec"])
        while cur < end - 0.01:
            remaining = end - cur
            seg_len = min(float(max_clip), remaining)
            # If remainder would leave a tiny trailing clip (<2.5s), absorb slightly only if within max_clip,
            # otherwise clamp cleanly to 2.0s minimum.
            seg_len = round(seg_len, 2)
            seg_end = round(cur + seg_len, 2)
            windows.append(
                SceneWindow(
                    scene_index=idx,
                    start_time_sec=round(cur, 2),
                    end_time_sec=seg_end,
                    duration_sec=seg_len,
                    character_id=interval.get("character_id"),
                    background_id=interval.get("background_id"),
                    is_master_scene=(idx == 1),
                )
            )
            cur = seg_end
            idx += 1

    return windows


def _words_per_second(language: str) -> float:
    lang = (language or "en-US").lower()
    if lang.startswith("vi"):
        return 2.7
    if lang.startswith(("ja", "zh", "ko")):
        return 3.5
    return 2.4


def estimate_speech_duration(dialogue: str, language: str = "en-US") -> float:
    """Estimate spoken audio duration in seconds from dialogue word/token count."""
    text = (dialogue or "").strip()
    if not text:
        return 0.0
    lang = (language or "en-US").lower()
    if lang.startswith(("ja", "zh", "ko")):
        chars = len(re.sub(r"\s+|[.,!?;:'\"()\-]", "", text))
        return round(chars / 4.2, 2)
    words = [w for w in re.split(r"\s+", re.sub(r"[.,!?;:'\"()\-—]", " ", text)) if w]
    if not words:
        return 0.0
    return round(len(words) / _words_per_second(language), 2)


def validate_scene_speech(
    dialogue: str, duration_sec: float, language: str = "en-US"
) -> tuple[float, Optional[str]]:
    """Check if spoken dialogue fits safely inside the target clip duration."""
    est = estimate_speech_duration(dialogue, language)
    safe_limit = max(1.0, duration_sec * 0.92)
    if est > safe_limit:
        wps = _words_per_second(language)
        max_words = max(1, math.floor(safe_limit * wps))
        return est, (
            f"Dialogue estimated at {est:.1f}s exceeds {duration_sec:.1f}s clip budget "
            f"(recommended <= {max_words} words)."
        )
    return est, None


def trim_dialogue_to_fit(dialogue: str, duration_sec: float, language: str = "en-US") -> str:
    """Trim dialogue cleanly to fit within duration_sec * 0.88."""
    text = (dialogue or "").strip()
    if not text:
        return ""
    est, warning = validate_scene_speech(text, duration_sec, language)
    if not warning:
        return text
    wps = _words_per_second(language)
    max_words = max(3, math.floor(duration_sec * 0.85 * wps))
    words = text.split()
    if len(words) <= max_words:
        return text
    trimmed = " ".join(words[:max_words]).rstrip(",;:-")
    if trimmed and trimmed[-1] not in ".!?":
        trimmed += "."
    return trimmed

