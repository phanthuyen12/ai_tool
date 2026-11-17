from app.services.ai_script.scene_planner import (
    estimate_speech_duration,
    parse_model_clip_duration,
    plan_scene_windows,
    trim_dialogue_to_fit,
    validate_scene_speech,
)


def test_parse_model_clip_duration():
    assert parse_model_clip_duration("omni-1.2-flash-8s-portrait") == 8
    assert parse_model_clip_duration("omni-1.2-flash-15s-portrait") == 15
    assert parse_model_clip_duration("veo-3.1-fast-i2v-6s-portrait") == 6
    assert parse_model_clip_duration("veo-3.1-lite-4s-landscape") == 4
    assert parse_model_clip_duration("unknown-model", default=8) == 8


def test_plan_scene_windows_with_character_schedule_and_remainder():
    # Spec Section III.3 & V.2:
    # 00:00 - 00:24 Expert
    # 00:24 - 00:40 Analyst
    # 00:40 - 01:00 Expert
    schedule = [
        {"start_sec": 0, "end_sec": 24, "character_id": "expert", "background_id": "office"},
        {"start_sec": 24, "end_sec": 40, "character_id": "analyst", "background_id": "office"},
        {"start_sec": 40, "end_sec": 60, "character_id": "expert", "background_id": "office"},
    ]
    windows = plan_scene_windows(
        target_duration_sec=60,
        clip_duration_sec=8,
        character_schedule=schedule,
        default_character_id="expert",
        default_background_id="office",
    )

    assert len(windows) == 8
    assert windows[0].is_master_scene is True
    assert windows[0].start_time_sec == 0.0
    assert windows[0].end_time_sec == 8.0
    assert windows[0].character_id == "expert"

    # 24-40 -> Analyst (2 scenes: 24-32, 32-40)
    assert windows[3].start_time_sec == 24.0
    assert windows[3].end_time_sec == 32.0
    assert windows[3].character_id == "analyst"
    assert windows[4].character_id == "analyst"

    # 40-60 -> Expert (8s + 8s + 4s remainder)
    assert windows[5].character_id == "expert"
    assert windows[7].start_time_sec == 56.0
    assert windows[7].end_time_sec == 60.0
    assert windows[7].duration_sec == 4.0


def test_speech_duration_warning_and_trimming():
    # ~40 seconds of dialogue inside an 8-second clip MUST raise a warning
    long_text = " ".join(["market structure liquidity order block confirmation"] * 18)
    est = estimate_speech_duration(long_text, "en-US")
    assert est > 30.0
    _, warning = validate_scene_speech(long_text, 8.0, "en-US")
    assert warning is not None

    trimmed = trim_dialogue_to_fit(long_text, 8.0, "en-US")
    est_after, warn_after = validate_scene_speech(trimmed, 8.0, "en-US")
    assert warn_after is None
    assert est_after <= 8.0

