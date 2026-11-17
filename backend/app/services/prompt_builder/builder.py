import re
from typing import Optional

from app.models.entities import Background, Character, CharacterRule, Project, Scene

PROMPT_LAYER_KEYS = [
    "global_rules",
    "character_rules",
    "background_rules",
    "scene_description",
    "continuity_instructions",
    "dialogue",
    "camera_instructions",
    "negative_constraints",
]

ASPECT_LABELS = {
    "9:16": "vertical 9:16",
    "16:9": "widescreen 16:9",
    "1:1": "square 1:1",
}

LANGUAGE_NAMES = {
    "en-US": "English",
    "en-GB": "British English",
    "vi-VN": "Vietnamese",
    "ja-JP": "Japanese",
    "ko-KR": "Korean",
    "zh-CN": "Mandarin Chinese",
}


def format_character_rules(character: Optional[Character], rule: Optional[CharacterRule]) -> str:
    if not character and not rule:
        return "Preserve natural presenter identity, realistic skin tone, and consistent wardrobe."
    parts: list[str] = []
    if character:
        desc = f"Subject: {character.name}"
        if character.age_appearance:
            desc += f", age appearance {character.age_appearance}"
        if character.role_title:
            desc += f", role: {character.role_title}"
        if character.description:
            desc += f" ({character.description})"
        parts.append(desc + ".")
    if rule:
        if rule.preserve_face:
            parts.append("Match the facial features and overall look shown in the character reference image.")
        if rule.preserve_hair_and_age:
            parts.append("Preserve original hairstyle and age appearance.")
        if rule.clothing_rule:
            parts.append(rule.clothing_rule.rstrip(".") + ".")
        if rule.preserve_skin_and_identity:
            parts.append("Maintain natural skin tone and identifying traits.")
        if rule.natural_movement_only:
            parts.append("Only subtle, natural human movement.")
        if rule.hand_gesture_level:
            parts.append(f"Hand gesture intensity: {rule.hand_gesture_level}.")
        if rule.custom_directives:
            parts.append(rule.custom_directives.strip())
    return " ".join(parts)


def format_background_rules(background: Optional[Background]) -> str:
    if not background:
        return "Maintain a clean, consistent studio environment and lighting."
    parts = [f"Environment: {background.name} ({background.style})."]
    if background.description:
        parts.append(background.description.strip())
    rules = background.preservation_rules_json or {}
    checks = [
        ("keep_desk_position", "Preserve exact desk and furniture placement"),
        ("keep_camera_angle", "Keep matching camera angle and framing"),
        ("keep_monitors", "Keep existing screens and background props intact"),
        ("keep_lighting", "Maintain identical ambient and key lighting"),
        ("keep_room_layout", "Do not alter room architecture or layout"),
        ("seat_character_properly", "Place the character naturally seated/positioned at the workstation"),
    ]
    enabled = [text for key, text in checks if rules.get(key)]
    if enabled:
        parts.append(". ".join(enabled) + ".")
    if rules.get("custom_rules"):
        parts.append(str(rules["custom_rules"]).strip())
    return " ".join(parts)


def compile_layers(layers: dict[str, str]) -> str:
    """Assemble non-empty layers in canonical order into a clean Flow2API prompt."""
    ordered = [layers.get(key, "").strip() for key in PROMPT_LAYER_KEYS]
    non_empty = [chunk for chunk in ordered if chunk]
    return "\n\n".join(non_empty)


def merge_locked_layers(
    generated: dict[str, str],
    existing_layers: Optional[dict[str, str]],
    locked_layers: Optional[list[str]],
) -> dict[str, str]:
    """Preserve user-locked layers verbatim when regenerating/recompiling prompts."""
    if not existing_layers or not locked_layers:
        return generated
    merged = dict(generated)
    for key in locked_layers:
        if key in PROMPT_LAYER_KEYS and key in existing_layers:
            merged[key] = existing_layers[key]
    return merged


def build_image_prompt_layers(
    *,
    scene: Scene,
    project: Project,
    character: Optional[Character],
    char_rule: Optional[CharacterRule],
    background: Optional[Background],
    is_master_scene: bool,
    has_prev_scene: bool = False,
    master_character_differs: bool = False,
    existing_layers: Optional[dict[str, str]] = None,
    locked_layers: Optional[list[str]] = None,
) -> tuple[dict[str, str], str]:
    """Build Prompt A (Image Generation) with 8 configurable layers."""
    aspect_text = ASPECT_LABELS.get(project.aspect_ratio, project.aspect_ratio)
    style_text = project.visual_style or "photorealistic"

    if is_master_scene:
        continuity = (
            "Establish this frame as the Master Visual Reference using the provided character and "
            "background reference images. Blend lighting, shadows, and perspective seamlessly."
        )
    elif master_character_differs:
        continuity = (
            f"Use the approved Master Image to preserve the exact office layout, lighting, color grading, "
            f"and camera perspective, while placing the newly referenced character accurately for Scene {scene.scene_index:02d}. "
            "Do not recreate or alter the room environment."
        )
    else:
        prev_clause = (
            " Also stay visually consistent with the previous approved scene frame."
            if has_prev_scene
            else ""
        )
        continuity = (
            "Use the approved master image as the primary continuity reference. "
            "Maintain the same character appearance, same workstation, same office layout, "
            "same lighting and matching camera angle. "
            f"Only modify the character's pose according to Scene {scene.scene_index:02d} requirements. "
            f"Do not recreate the environment.{prev_clause}"
        )

    neg_parts = ["No extra people", "no style shift", "no text overlay", "no watermark", "no distorted anatomy"]
    if char_rule and char_rule.no_extra_characters:
        neg_parts[0] = "Strictly single presenter only, no extra characters"
    bg_rules = (background.preservation_rules_json if background else {}) or {}
    if bg_rules.get("no_extra_objects", True):
        neg_parts.append("no unnecessary added objects")

    generated = {
        "global_rules": (
            f"Create a {style_text} still frame using the provided character and environment references. "
            f"{project.global_rules.strip()}"
        ).strip(),
        "character_rules": format_character_rules(character, char_rule),
        "background_rules": format_background_rules(background),
        "scene_description": (
            f"Scene {scene.scene_index:02d} ({scene.title}): "
            f"{scene.character_action or 'Presenter positioned calmly facing the camera.'}"
        ),
        "continuity_instructions": continuity,
        "dialogue": "",  # Image prompt carries visual mood; spoken words live in Video Prompt B
        "camera_instructions": f"{scene.camera_motion or 'Medium shot, eye-level camera'}, {aspect_text}, {style_text} photography.",
        "negative_constraints": "Negative constraints: " + ", ".join(neg_parts) + ".",
    }
    final_layers = merge_locked_layers(generated, existing_layers, locked_layers)
    return final_layers, compile_layers(final_layers)


def build_video_prompt_layers(
    *,
    scene: Scene,
    project: Project,
    character: Optional[Character],
    char_rule: Optional[CharacterRule],
    background: Optional[Background],
    existing_layers: Optional[dict[str, str]] = None,
    locked_layers: Optional[list[str]] = None,
) -> tuple[dict[str, str], str]:
    """Build Prompt B (Video Generation) with 8 configurable layers."""
    lang_label = LANGUAGE_NAMES.get(project.language, project.language)
    voice_desc = (
        (project.timeline_config_json or {}).get("voice_description")
        or (character.voice_style if character else "clear natural presenter voice")
    )
    clean_dialogue = (scene.dialogue or "").strip()

    if project.voice_mode == "ai_generated" and clean_dialogue:
        dialogue_layer = (
            f"The character speaks ONLY in {lang_label} with a {voice_desc}. "
            f"Keep every spoken word in {lang_label}; do not switch languages, translate, paraphrase, or add extra speech. "
            "Speak the quoted dialogue for this scene exactly once, as a complete, self-contained line - start and finish "
            "it naturally within this clip. Continue the topic without repeating the previous scene, but do not recap, "
            "replay, or duplicate any line, and do not add a greeting or closing unless it is in the quote. "
            "Accurate lip synchronization to the exact dialogue, natural blinking, and subtle realistic gestures.\n"
            f'Spoken dialogue:\n"{clean_dialogue}"'
        )
    elif project.voice_mode == "silent":
        dialogue_layer = (
            "No spoken dialogue anywhere in this clip, no narration, no captions, ambient room tone only. "
            "Follow exactly the physical action described in the scene description above - do not add "
            "explanatory gestures, nodding, or presenter-style delivery unless explicitly described."
        )
    else:
        dialogue_layer = (
            "Silent presenter delivery: natural subtle breathing, blinking, and gentle nod/hand gestures, "
            "no spoken dialogue, ambient room tone only."
        )

    neg_parts = ["no scene transitions", "no sudden camera cuts", "no extra characters", "no appearance drift between scenes"]
    if (char_rule is None or char_rule.no_auto_bgm) and "no background music" not in project.global_rules.lower():
        neg_parts.append("no background music")
    if project.voice_mode == "ai_generated" and clean_dialogue:
        neg_parts.append("clear speech")

    static_cam = char_rule.static_camera_on_speech if char_rule else True
    cam_text = (
        f"{scene.camera_motion or 'Static eye-level camera'}, locked tripod stability, smooth {scene.planned_duration_sec:.0f}s continuous shot."
        if static_cam
        else f"{scene.camera_motion or 'Subtle steady framing'}, continuous {scene.planned_duration_sec:.0f}s shot."
    )
    continuity_text = (
        "Veo R2V reference hierarchy: image 1 is the exact opening frame for this scene; remaining images are appearance/style anchors only. "
        "Keep the same presenter face, age, hair, clothing, and the same room/set as the approved master. "
        "Do not combine or alternate reference images, add characters, redesign the background, or change camera side. "
        "Use one continuous shot with the same framing and lighting. Speak the current scene's quoted line once only; never repeat or recap prior dialogue."
        if "veo" in (project.video_model or "").lower() and "r2v" in (project.video_model or "").lower()
        else "Preserve the original character appearance, clothing, background layout, camera position, and lighting as closely as possible throughout the clip."
    )

    generated = {
        "global_rules": (
            "Animate the supplied approved reference image. "
            f"{project.global_rules.strip()}"
        ).strip(),
        "character_rules": format_character_rules(character, char_rule),
        "background_rules": "Preserve the exact background environment, workstation, lighting, and framing from the reference image.",
        "scene_description": f"Action ({scene.planned_duration_sec:.1f}s): {scene.character_action or 'Natural calm studio presentation.'}",
        "continuity_instructions": continuity_text,
        "dialogue": dialogue_layer,
        "camera_instructions": cam_text,
        "negative_constraints": "Constraints: " + ", ".join(neg_parts) + ".",
    }
    final_layers = merge_locked_layers(generated, existing_layers, locked_layers)
    return final_layers, compile_layers(final_layers)


def adjust_video_prompt_for_audio_filter_retry(
    prompt: str, attempt_number: int, keep_dialogue: bool
) -> str:
    """
    Implement Spec VIII.5 retry rule for PUBLIC_ERROR_AUDIO_FILTERED:
    - When user requested AI spoken narration (keep_dialogue=True), NEVER delete the dialogue;
      soften surrounding audio framing and request neutral clean speech acoustics.
    - Only add 'no dialogue, ambient sound only' when there is no spoken dialogue or TTS mode is external.
    """
    if not keep_dialogue:
        suffix = ", no dialogue, ambient sound only, no speech"
        if suffix not in prompt:
            return f"{prompt.rstrip()}\n\n{suffix.lstrip(', ')}"
        return prompt

    adjusted = prompt
    # Soften potentially trigger-heavy descriptive words outside quoted dialogue
    softened_words = {
        r"\bscream(?:ing)?\b": "speaking calmly",
        r"\bshout(?:ing)?\b": "explaining steadily",
        r"\bexplosive\b": "significant",
        r"\bcrash(?:ing)?\b": "sharp drop",
    }
    for pattern, repl in softened_words.items():
        adjusted = re.sub(pattern, repl, adjusted, flags=re.IGNORECASE)

    if attempt_number <= 2:
        hint = "Audio direction: calm neutral educational broadcast tone, clean studio speech audio, gentle natural room acoustics."
    else:
        hint = "Audio direction: very calm steady studio monologue, minimal ambient noise, clean neutral speech delivery."
    return f"{adjusted.rstrip()}\n\n{hint}"
