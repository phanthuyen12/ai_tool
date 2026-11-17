import subprocess
from pathlib import Path

from app.services.media_storage.base import TempWorkspaceManager
from app.services.video_editor.composer import (
    TimelineClipInput,
    aspect_ratio_matches,
    compose_timeline_video,
    probe_media,
)


def _make_synthetic_clip(dest: Path, duration: float, freq: int, color: str) -> Path:
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s=720x1280:d={duration}:r=30",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq}:sample_rate=44100:duration={duration}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(dest),
    ]
    subprocess.run(cmd, check=True, timeout=30)
    return dest


def test_asset_aspect_validation_detects_portrait_in_landscape_project():
    assert aspect_ratio_matches(720, 1280, "9:16")
    assert aspect_ratio_matches(1920, 1080, "16:9")
    assert not aspect_ratio_matches(720, 1280, "16:9")


def test_ffmpeg_compose_three_clips_and_cleanup():
    workspace_path: Path
    with TempWorkspaceManager(prefix="test_ffmpeg") as tmp_dir:
        workspace_path = tmp_dir
        c1 = _make_synthetic_clip(tmp_dir / "clip1.mp4", 2.0, 440, "navy")
        c2 = _make_synthetic_clip(tmp_dir / "clip2.mp4", 2.5, 550, "teal")
        c3 = _make_synthetic_clip(tmp_dir / "clip3.mp4", 1.5, 660, "maroon")

        out_mp4 = tmp_dir / "final.mp4"
        out_srt = tmp_dir / "final.srt"
        out_thumb = tmp_dir / "thumb.jpg"

        stats = compose_timeline_video(
            clips=[
                TimelineClipInput(scene_index=1, video_path=c1, caption_text="Scene one intro"),
                TimelineClipInput(scene_index=2, video_path=c2, caption_text="Scene two analysis"),
                TimelineClipInput(scene_index=3, video_path=c3, caption_text="Scene three wrap up"),
            ],
            workspace_dir=tmp_dir,
            output_mp4_path=out_mp4,
            output_srt_path=out_srt,
            output_thumb_path=out_thumb,
            aspect_ratio="9:16",
            export_quality="1080p",
            burn_subtitles=True,
            default_transition="none",
        )

        assert out_mp4.exists()
        assert out_srt.exists()
        assert out_thumb.exists()
        assert stats["resolution"] == "1080x1920"

        final_probe = probe_media(out_mp4)
        assert final_probe.width == 1080
        assert final_probe.height == 1920
        assert final_probe.has_audio is True
        assert final_probe.video_codec == "h264"
        assert final_probe.audio_codec == "aac"
        assert 5.5 <= final_probe.duration <= 6.5

        srt_content = out_srt.read_text(encoding="utf-8")
        assert "Scene one intro" in srt_content
        assert "Scene three wrap up" in srt_content

    # TempWorkspaceManager must auto-clean temporary directory on exit
    assert not workspace_path.exists()


def test_ffmpeg_compose_with_crossfade_transition():
    with TempWorkspaceManager(prefix="test_xfade") as tmp_dir:
        c1 = _make_synthetic_clip(tmp_dir / "c1.mp4", 2.0, 440, "navy")
        c2 = _make_synthetic_clip(tmp_dir / "c2.mp4", 2.0, 520, "darkgreen")
        c3 = _make_synthetic_clip(tmp_dir / "c3.mp4", 2.0, 620, "purple")

        out_mp4 = tmp_dir / "xfade.mp4"
        out_srt = tmp_dir / "xfade.srt"
        out_thumb = tmp_dir / "xfade.jpg"

        compose_timeline_video(
            clips=[
                TimelineClipInput(scene_index=1, video_path=c1, caption_text="Part 1"),
                TimelineClipInput(scene_index=2, video_path=c2, caption_text="Part 2"),
                TimelineClipInput(scene_index=3, video_path=c3, caption_text="Part 3"),
            ],
            workspace_dir=tmp_dir,
            output_mp4_path=out_mp4,
            output_srt_path=out_srt,
            output_thumb_path=out_thumb,
            aspect_ratio="16:9",
            burn_subtitles=False,
            default_transition="crossfade",
            transition_duration_sec=0.4,
        )
        p = probe_media(out_mp4)
        assert p.width == 1920
        assert p.height == 1080
        assert p.has_audio is True
        # 2.0 + 2.0 + 2.0 - 2 * 0.4 = 5.2s
        assert 4.8 <= p.duration <= 5.6
