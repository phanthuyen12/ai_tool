from app.models.entities import Background, Character, CharacterRule, Project, Scene
from app.services.prompt_builder.builder import (
    PROMPT_LAYER_KEYS,
    adjust_video_prompt_for_audio_filter_retry,
    build_image_prompt_layers,
    build_video_prompt_layers,
)


def _sample_project() -> Project:
    return Project(
        id="proj-1",
        owner_id="user-1",
        name="Forex XAUUSD Education",
        topic="Smart Money Concepts",
        language="en-US",
        target_duration_seconds=60,
        aspect_ratio="9:16",
        visual_style="photorealistic",
        voice_mode="ai_generated",
        global_rules="Keep studio consistency across scenes.",
    )


def _sample_char_and_bg():
    char = Character(
        id="char-1",
        owner_id="user-1",
        name="Senior Forex Expert",
        age_appearance="60",
        voice_style="Mature American male",
        role_title="Presenter",
    )
    rule = CharacterRule(
        owner_id="user-1",
        character_id="char-1",
        preserve_face=True,
        preserve_hair_and_age=True,
        clothing_rule="Preserve dark tailored blazer",
        natural_movement_only=True,
        no_extra_characters=True,
        hand_gesture_level="moderate",
        static_camera_on_speech=True,
        no_auto_bgm=True,
    )
    bg = Background(
        id="bg-1",
        owner_id="user-1",
        name="Modern Trading Office",
        style="photorealistic",
        preservation_rules_json={
            "keep_desk_position": True,
            "keep_camera_angle": True,
            "keep_monitors": True,
            "keep_lighting": True,
            "keep_room_layout": True,
            "seat_character_properly": True,
            "no_extra_objects": True,
        },
    )
    return char, rule, bg


def test_build_image_prompt_master_and_continuity_layers():
    project = _sample_project()
    char, rule, bg = _sample_char_and_bg()

    scene1 = Scene(
        id="s1",
        project_id=project.id,
        script_id="sc1",
        scene_index=1,
        title="Hook",
        planned_duration_sec=8.0,
        character_action="Seated behind trading desk looking toward camera.",
        camera_motion="Medium shot, eye-level camera",
        is_master_scene=True,
    )
    layers1, compiled1 = build_image_prompt_layers(
        scene=scene1,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
        is_master_scene=True,
    )
    assert set(layers1.keys()) == set(PROMPT_LAYER_KEYS)
    assert "Master Visual Reference" in layers1["continuity_instructions"]
    assert "vertical 9:16" in compiled1
    assert "Senior Forex Expert" in compiled1

    scene2 = Scene(
        id="s2",
        project_id=project.id,
        script_id="sc1",
        scene_index=2,
        title="Liquidity Sweep",
        planned_duration_sec=8.0,
        character_action="Gesturing slightly toward chart monitors.",
        camera_motion="Medium shot, eye-level camera",
        is_master_scene=False,
    )
    layers2, compiled2 = build_image_prompt_layers(
        scene=scene2,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
        is_master_scene=False,
        has_prev_scene=False,
    )
    assert "primary continuity reference" in layers2["continuity_instructions"]
    assert "Do not recreate the environment" in compiled2


def test_locked_prompt_layers_are_preserved():
    project = _sample_project()
    char, rule, bg = _sample_char_and_bg()
    scene = Scene(
        id="s1",
        project_id=project.id,
        script_id="sc1",
        scene_index=1,
        title="Intro",
        planned_duration_sec=8.0,
        dialogue="Today we analyze XAUUSD.",
    )

    locked = ["camera_instructions", "dialogue"]
    existing = {
        "camera_instructions": "LOCKED CUSTOM CAMERA: 50mm lens, strict tripod.",
        "dialogue": 'Spoken dialogue: "Custom locked dialogue line."',
    }
    layers, compiled = build_video_prompt_layers(
        scene=scene,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
        existing_layers=existing,
        locked_layers=locked,
    )
    assert layers["camera_instructions"] == existing["camera_instructions"]
    assert layers["dialogue"] == existing["dialogue"]
    assert "Custom locked dialogue line." in compiled


def test_veo_r2v_prompt_sets_identity_reference_hierarchy():
    project = _sample_project()
    project.video_model = "veo-3.1-fast-r2v-8s-landscape"
    project.language = "vi-VN"
    char, rule, bg = _sample_char_and_bg()
    scene = Scene(
        id="s2",
        project_id=project.id,
        script_id="sc1",
        scene_index=2,
        title="Continue narration",
        planned_duration_sec=8.0,
        dialogue="...và tiếp tục giải thích nội dung.",
    )

    layers, compiled = build_video_prompt_layers(
        scene=scene,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
    )
    assert "Veo R2V reference hierarchy" in layers["continuity_instructions"]
    assert "same presenter face" in compiled
    assert "ONLY in Vietnamese" in layers["dialogue"]
    assert "exactly once" in layers["dialogue"]
    assert "do not recap" in layers["dialogue"]


def test_deep_work_mode_is_silent_and_keeps_action_focused():
    project = _sample_project()
    project.global_rules = "[DEEP_WORK_MODE]"
    char, rule, bg = _sample_char_and_bg()
    scene = Scene(
        id="s-deep-work",
        project_id=project.id,
        script_id="sc1",
        scene_index=1,
        title="Focused work",
        planned_duration_sec=8.0,
        dialogue="This dialogue must not be used.",
        character_action="",
    )
    layers, compiled = build_video_prompt_layers(
        scene=scene,
        project=project,
        character=char,
        char_rule=rule,
        background=bg,
    )
    assert "Completely silent deep-work performance" in layers["dialogue"]
    assert "brief thought" in layers["dialogue"]
    assert "focused deep work" in layers["scene_description"]
    assert "This dialogue must not be used" not in compiled


def test_audio_filter_retry_preserves_requested_dialogue():
    original = (
        "Animate reference image.\n\n"
        'Spoken dialogue:\n"Today, we are analyzing the XAUUSD market."\n\n'
        "Static camera, clear speech."
    )
    retried_with_voice = adjust_video_prompt_for_audio_filter_retry(
        original, attempt_number=2, keep_dialogue=True
    )
    assert 'Today, we are analyzing the XAUUSD market.' in retried_with_voice
    assert "no dialogue" not in retried_with_voice.lower()

    retried_silent = adjust_video_prompt_for_audio_filter_retry(
        "Animate presenter silently.", attempt_number=2, keep_dialogue=False
    )
    assert "no dialogue, ambient sound only" in retried_silent
