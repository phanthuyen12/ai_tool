import json
import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_settings

logger = logging.getLogger(__name__)

ASPECT_RESOLUTIONS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
}


@dataclass
class MediaProbeResult:
    duration: float
    width: Optional[int]
    height: Optional[int]
    has_audio: bool
    video_codec: Optional[str]
    audio_codec: Optional[str]


@dataclass
class TimelineClipInput:
    scene_index: int
    video_path: Path
    caption_text: str
    trim_start_sec: float = 0.0
    trim_end_sec: Optional[float] = None
    external_audio_path: Optional[Path] = None
    transition_type: str = "none"


def resolve_target_resolution(aspect_ratio: str, quality: str = "1080p") -> tuple[int, int]:
    base_w, base_h = ASPECT_RESOLUTIONS.get(aspect_ratio, (1080, 1920))
    if quality == "720p":
        return (int(base_w * 2 / 3), int(base_h * 2 / 3))
    return (base_w, base_h)


def aspect_ratio_matches(width: Optional[int], height: Optional[int], aspect_ratio: str) -> bool:
    """Return whether stored media fits the target canvas without pillar/letterboxing."""
    if not width or not height or aspect_ratio == "1:1":
        return True
    expected = {"9:16": 9 / 16, "16:9": 16 / 9}.get(aspect_ratio)
    if not expected:
        return True
    return abs(math.log((width / height) / expected)) <= 0.16


def probe_media(file_path: Path) -> MediaProbeResult:
    """Inspect video/audio file metadata using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    parsed = json.loads(proc.stdout or "{}")
    fmt = parsed.get("format", {})
    streams = parsed.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or v_stream.get("duration") or 0.0)
    return MediaProbeResult(
        duration=round(duration, 3),
        width=v_stream.get("width"),
        height=v_stream.get("height"),
        has_audio=a_stream is not None,
        video_codec=v_stream.get("codec_name"),
        audio_codec=a_stream.get("codec_name") if a_stream else None,
    )


def _format_srt_timestamp(seconds: float) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    minutes = (ms_total % 3_600_000) // 60_000
    secs = (ms_total % 60_000) // 1000
    ms = ms_total % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def generate_srt_file(
    segments: list[tuple[float, float, str]], output_path: Path
) -> Path:
    """Write standard UTF-8 .srt subtitle file from (start_sec, end_sec, text) entries."""
    lines: list[str] = []
    idx = 1
    for start_s, end_s, text in segments:
        clean = (text or "").strip()
        if not clean or end_s <= start_s:
            continue
        lines.append(str(idx))
        lines.append(f"{_format_srt_timestamp(start_s)} --> {_format_srt_timestamp(end_s)}")
        lines.append(clean)
        lines.append("")
        idx += 1
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _run_ffmpeg(args: list[str], cwd: Optional[Path] = None, timeout: int = 600) -> str:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed (code {proc.returncode}): {proc.stderr[-1200:]}")
    return proc.stderr


def normalize_clip_segment(
    *,
    clip: TimelineClipInput,
    output_path: Path,
    width: int,
    height: int,
    voice_muted: bool = False,
    voice_volume: float = 1.0,
) -> float:
    """Normalize a single scene clip to uniform H.264 30fps + AAC 44100Hz stereo."""
    settings = get_settings()
    probe = probe_media(clip.video_path)
    start_sec = max(0.0, float(clip.trim_start_sec or 0.0))
    raw_end = float(clip.trim_end_sec) if clip.trim_end_sec and clip.trim_end_sec > start_sec else probe.duration
    clip_dur = max(0.25, min(probe.duration - start_sec, raw_end - start_sec)) if probe.duration > 0 else 4.0

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "setsar=1,fps=30,format=yuv420p"
    )

    inputs: list[str] = ["-ss", f"{start_sec:.3f}", "-t", f"{clip_dur:.3f}", "-i", str(clip.video_path)]
    audio_map_args: list[str] = []

    if clip.external_audio_path and not voice_muted:
        inputs.extend(["-i", str(clip.external_audio_path)])
        audio_map_args = [
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-af",
            f"volume={voice_volume:.2f},aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,apad",
            "-shortest",
        ]
    elif probe.has_audio and not voice_muted:
        audio_map_args = [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-af",
            f"volume={voice_volume:.2f},aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo",
        ]
    else:
        inputs.extend(["-f", "lavfi", "-t", f"{clip_dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
        audio_map_args = ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]

    args = [
        *inputs,
        "-vf",
        vf,
        *audio_map_args,
        "-c:v",
        "libx264",
        "-preset",
        settings.ffmpeg_preset,
        "-crf",
        str(settings.ffmpeg_crf),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-threads",
        str(settings.ffmpeg_threads),
        str(output_path),
    ]
    _run_ffmpeg(args)
    out_probe = probe_media(output_path)
    return out_probe.duration or clip_dur


#: A single, fixed, subtle color-grading pass applied uniformly across the whole
#: stitched video (never per-clip). Independently generated scene clips can each
#: drift slightly in white balance/tint; this doesn't "fix" any one clip, but it
#: lays an identical gentle grade over every frame so the finished video reads as
#: one consistent look instead of visibly different tones cutting between scenes.
COLOR_GRADE_FILTER = (
    "eq=contrast=1.03:saturation=1.05,"
    "colorbalance=rs=0.02:gs=0.0:bs=-0.02:rm=0.015:gm=0.0:bm=-0.015:rh=0.01:gh=0.0:bh=-0.01"
)


def compose_timeline_video(
    *,
    clips: list[TimelineClipInput],
    workspace_dir: Path,
    output_mp4_path: Path,
    output_srt_path: Path,
    output_thumb_path: Path,
    aspect_ratio: str = "9:16",
    export_quality: str = "1080p",
    voice_muted: bool = False,
    voice_volume: float = 1.0,
    bgm_path: Optional[Path] = None,
    bgm_muted: bool = False,
    bgm_volume: float = 0.15,
    watermark_path: Optional[Path] = None,
    burn_subtitles: bool = False,
    default_transition: str = "none",
    transition_duration_sec: float = 0.4,
    intro_path: Optional[Path] = None,
    outro_path: Optional[Path] = None,
    apply_color_grade: bool = True,
) -> dict[str, Any]:
    """
    Full FFmpeg render pipeline:
    1. Optional Intro + Normalized Scene Clips + Optional Outro
    2. Concatenation (fast concat demuxer or xfade/acrossfade)
    3. Accurate SRT generation + optional BGM mix + optional watermark + optional burned subtitles
    4. H.264/AAC faststart MP4 + thumbnail extraction
    """
    if not clips:
        raise ValueError("At least 1 scene video clip is required to render")

    settings = get_settings()
    width, height = resolve_target_resolution(aspect_ratio, export_quality)
    logs: list[str] = []

    ordered_segments: list[tuple[Path, float, str]] = []
    if intro_path and intro_path.exists():
        intro_norm = workspace_dir / "seg_intro.mp4"
        dur = normalize_clip_segment(
            clip=TimelineClipInput(scene_index=0, video_path=intro_path, caption_text=""),
            output_path=intro_norm,
            width=width,
            height=height,
            voice_muted=False,
            voice_volume=1.0,
        )
        ordered_segments.append((intro_norm, dur, ""))

    for idx, clip in enumerate(clips):
        seg_path = workspace_dir / f"seg_{idx:03d}.mp4"
        dur = normalize_clip_segment(
            clip=clip,
            output_path=seg_path,
            width=width,
            height=height,
            voice_muted=voice_muted,
            voice_volume=voice_volume,
        )
        ordered_segments.append((seg_path, dur, clip.caption_text))

    if outro_path and outro_path.exists():
        outro_norm = workspace_dir / "seg_outro.mp4"
        dur = normalize_clip_segment(
            clip=TimelineClipInput(scene_index=999, video_path=outro_path, caption_text=""),
            output_path=outro_norm,
            width=width,
            height=height,
            voice_muted=False,
            voice_volume=1.0,
        )
        ordered_segments.append((outro_norm, dur, ""))

    use_xfade = default_transition in ("fade", "crossfade") and len(ordered_segments) > 1
    trans_d = max(0.15, min(1.0, float(transition_duration_sec))) if use_xfade else 0.0

    # Compute SRT intervals aligned with final playback clock
    srt_entries: list[tuple[float, float, str]] = []
    cursor = 0.0
    for i, (_, seg_dur, caption) in enumerate(ordered_segments):
        eff_dur = seg_dur
        if use_xfade and i < len(ordered_segments) - 1:
            eff_dur = max(0.2, seg_dur - trans_d)
        if caption.strip():
            srt_entries.append((cursor, cursor + seg_dur - (trans_d if use_xfade and i < len(ordered_segments) - 1 else 0.0), caption))
        cursor += eff_dur
    generate_srt_file(srt_entries, output_srt_path)

    stitched_path = workspace_dir / "stitched.mp4"
    if len(ordered_segments) == 1:
        stitched_path.write_bytes(ordered_segments[0][0].read_bytes())
    elif not use_xfade:
        concat_list = workspace_dir / "concat.txt"
        concat_lines = [f"file '{seg_path.name}'" for seg_path, _, _ in ordered_segments]
        concat_list.write_text("\n".join(concat_lines), encoding="utf-8")
        logs.append(
            _run_ffmpeg(
                ["-f", "concat", "-safe", "0", "-i", concat_list.name, "-c", "copy", stitched_path.name],
                cwd=workspace_dir,
            )
        )
    else:
        # Build xfade + acrossfade chain
        inputs: list[str] = []
        for seg_path, _, _ in ordered_segments:
            inputs.extend(["-i", seg_path.name])
        filter_parts: list[str] = []
        prev_v = "[0:v]"
        prev_a = "[0:a]"
        running_offset = ordered_segments[0][1] - trans_d
        for i in range(1, len(ordered_segments)):
            out_v = f"[v{i}]"
            out_a = f"[a{i}]"
            filter_parts.append(
                f"{prev_v}[{i}:v]xfade=transition=fade:duration={trans_d:.3f}:offset={max(0.1, running_offset):.3f}{out_v}"
            )
            filter_parts.append(
                f"{prev_a}[{i}:a]acrossfade=d={trans_d:.3f}{out_a}"
            )
            prev_v = out_v
            prev_a = out_a
            if i < len(ordered_segments) - 1:
                running_offset += ordered_segments[i][1] - trans_d

        logs.append(
            _run_ffmpeg(
                [
                    *inputs,
                    "-filter_complex",
                    ";".join(filter_parts),
                    "-map",
                    prev_v,
                    "-map",
                    prev_a,
                    "-c:v",
                    "libx264",
                    "-preset",
                    settings.ffmpeg_preset,
                    "-crf",
                    str(settings.ffmpeg_crf),
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-threads",
                    str(settings.ffmpeg_threads),
                    stitched_path.name,
                ],
                cwd=workspace_dir,
            )
        )

    has_subtitles = burn_subtitles and bool(srt_entries)
    has_bgm = bool(bgm_path and bgm_path.exists() and not bgm_muted)
    has_watermark = bool(watermark_path and watermark_path.exists())
    has_color_grade = bool(apply_color_grade)

    if not (has_subtitles or has_bgm or has_watermark or has_color_grade):
        logs.append(
            _run_ffmpeg(
                ["-i", stitched_path.name, "-c", "copy", "-movflags", "+faststart", str(output_mp4_path)],
                cwd=workspace_dir,
            )
        )
    else:
        inputs = ["-i", stitched_path.name]
        v_chain = "[0:v]"
        v_map = "0:v"
        a_chain = "[0:a]"
        a_map = "0:a"
        filters: list[str] = []
        next_idx = 1

        if has_color_grade:
            filters.append(f"{v_chain}{COLOR_GRADE_FILTER}[vgrade]")
            v_chain = "[vgrade]"
            v_map = "[vgrade]"

        if has_watermark and watermark_path is not None:
            wm_idx = next_idx
            next_idx += 1
            inputs.extend(["-i", str(watermark_path)])
            filters.append(f"[{wm_idx}:v]scale=140:-1[wm]")
            filters.append(f"{v_chain}[wm]overlay=W-w-32:32[vwm]")
            v_chain = "[vwm]"
            v_map = "[vwm]"

        if has_subtitles:
            style = (
                "FontName=Arial,FontSize=20,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BackColour=&H64000000,BorderStyle=3,Outline=1,MarginV=42"
            )
            filters.append(f"{v_chain}subtitles={output_srt_path.name}:force_style='{style}'[vsub]")
            v_chain = "[vsub]"
            v_map = "[vsub]"

        if has_bgm and bgm_path is not None:
            bgm_idx = next_idx
            next_idx += 1
            inputs.extend(["-stream_loop", "-1", "-i", str(bgm_path)])
            filters.append(f"[{bgm_idx}:a]volume={bgm_volume:.2f},aresample=44100[bgm]")
            filters.append(f"{a_chain}[bgm]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[amixed]")
            a_chain = "[amixed]"
            a_map = "[amixed]"

        # Copy SRT into workspace_dir if not already there so relative subtitles=filename works
        local_srt = workspace_dir / output_srt_path.name
        if local_srt.resolve() != output_srt_path.resolve():
            local_srt.write_bytes(output_srt_path.read_bytes())

        args = [
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            v_map,
            "-map",
            a_map,
            "-c:v",
            "libx264",
            "-preset",
            settings.ffmpeg_preset,
            "-crf",
            str(settings.ffmpeg_crf),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-threads",
            str(settings.ffmpeg_threads),
            str(output_mp4_path),
        ]
        logs.append(_run_ffmpeg(args, cwd=workspace_dir))

    # Extract clean thumbnail frame
    _run_ffmpeg(
        ["-ss", "0.5", "-i", str(output_mp4_path), "-vframes", "1", "-q:v", "2", str(output_thumb_path)]
    )
    final_probe = probe_media(output_mp4_path)
    return {
        "duration_seconds": final_probe.duration,
        "width": final_probe.width,
        "height": final_probe.height,
        "resolution": f"{final_probe.width or width}x{final_probe.height or height}",
        "file_size_bytes": output_mp4_path.stat().st_size,
        "ffmpeg_log_excerpt": "\n".join(l for l in logs if l).strip()[-2000:],
    }
