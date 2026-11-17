import json
import logging
import math
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from app.services.ai_script.scene_planner import (
    SceneWindow,
    estimate_speech_duration,
    trim_dialogue_to_fit,
    validate_scene_speech,
)
from app.services.provider_config import EffectiveRuntimeConfig

logger = logging.getLogger(__name__)


@dataclass
class GeneratedSceneDraft:
    scene_index: int
    title: str
    dialogue: str
    caption_text: str
    character_action: str
    camera_motion: str
    transition_type: str


@dataclass
class GeneratedScriptDraft:
    title: str
    objective: str
    opening_hook: str
    narration_summary: str
    llm_provider_used: str
    scenes: list[GeneratedSceneDraft]
    raw_payload: dict[str, Any]


def _extract_json_block(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S)
    if fence:
        cleaned = fence.group(1)
    else:
        brace = re.search(r"(\{.*\})", cleaned, flags=re.S)
        if brace:
            cleaned = brace.group(1)
    return json.loads(cleaned)


def _build_llm_prompt(
    *,
    topic: str,
    topic_category: str,
    goal_description: str,
    language: str,
    visual_style: str,
    windows: list[SceneWindow],
    character_names: dict[Optional[str], str],
    voice_mode: str = "ai_generated",
    script_mode: str = "ai_presenter",
) -> str:
    is_silent = voice_mode == "silent"
    is_literal = script_mode == "literal"
    slots_desc = []
    for w in windows:
        max_words = max(4, math.floor(w.duration_sec * 2.1))
        char_name = character_names.get(w.character_id, "Presenter")
        if is_silent or is_literal:
            slots_desc.append(
                f"- Scene {w.scene_index}: {w.start_time_sec:.0f}s to {w.end_time_sec:.0f}s "
                f"(duration {w.duration_sec:.1f}s, subject: {char_name})"
            )
        else:
            slots_desc.append(
                f"- Scene {w.scene_index}: {w.start_time_sec:.0f}s to {w.end_time_sec:.0f}s "
                f"(duration {w.duration_sec:.1f}s, presenter: {char_name}, STRICT max spoken words: {max_words})"
            )

    slots_block = "\n".join(slots_desc)

    if is_literal:
        if is_silent:
            dialogue_rule = (
                "ABSOLUTE RULE: There is no spoken dialogue anywhere in this video. The \"dialogue\" and "
                "\"caption_text\" fields MUST be empty strings for every scene. Do not invent any lines, "
                "narration, or captions."
            )
        else:
            dialogue_rule = (
                f"ABSOLUTE RULE: Do NOT invent, expand, summarize, or add any dialogue beyond the exact text "
                f"given below. Split that exact text verbatim across the scene slots in chronological order, "
                f"preserving the original wording in {language}. Do not add greetings, closings, or extra "
                "sentences that are not already in the text."
            )
        return (
            "You are a cinematographer/editor breaking the user's OWN script or scene description into a "
            "shot-by-shot timeline for video generation. Do not reinterpret it as a topic to explain and do "
            "not invent new content beyond what is given.\n"
            f"User's exact script/scene description: {topic}\n"
            f"Extra direction: {goal_description or 'None'}\n"
            f"Visual Style: {visual_style}\n\n"
            f"{dialogue_rule}\n"
            "CONTINUITY RULE: Treat all scenes as consecutive slices of ONE continuous action/script, not "
            "separate standalone clips. Split the text across the scene slots in chronological order so it "
            "progresses naturally from one slot to the next. Do not restart, repeat, or summarize prior scenes; "
            "each scene continues exactly where the previous one left off.\n"
            "The \"character_action\" field describes precisely and only the visible physical action/pose/"
            "expression happening in that time slice, using only details already present above. Do not describe "
            "the subject as presenting to an audience or gesturing to explain, unless the text explicitly says so.\n\n"
            "You MUST produce an exact JSON object matching the exact scene timeline below.\n"
            f"Scene timeline slots:\n{slots_block}\n\n"
            "Return ONLY valid JSON with schema:\n"
            "{\n"
            '  "title": "string",\n'
            '  "objective": "string",\n'
            '  "opening_hook": "string",\n'
            '  "narration_summary": "string",\n'
            '  "scenes": [\n'
            "    {\n"
            '      "scene_index": 1,\n'
            '      "title": "short scene label",\n'
            '      "dialogue": "exact verbatim excerpt for this slice, or empty if silent",\n'
            '      "caption_text": "same as dialogue, or empty if silent",\n'
            '      "character_action": "precise visible action for this time slice",\n'
            '      "camera_motion": "Static eye-level medium shot",\n'
            '      "transition_type": "none"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

    return (
        "You are a senior video scriptwriter for short-form and educational AI presenter videos.\n"
        f"Topic: {topic}\n"
        f"Category: {topic_category}\n"
        f"Goal: {goal_description or 'Clear, practical, high-retention educational script'}\n"
        f"Spoken Language: {language}\n"
        f"Visual Style: {visual_style}\n\n"
        f"CRITICAL LANGUAGE RULE: Write every spoken dialogue line and caption ONLY in {language}. "
        "Do not mix English and Vietnamese or translate technical product names unless they are part of the topic. "
        "Keep scene dialogue exactly in the requested spoken language for consistent voice generation and lip sync.\n"
        "NARRATIVE CONTINUITY RULE: Treat all scenes as one connected script, not separate standalone clips. "
        "Use a clear progression (hook, explain, steps/evidence, practical takeaway, concise conclusion). "
        "Each scene must introduce one new point and continue naturally from the previous scene. "
        "CRITICAL: each scene's dialogue MUST be one or more complete sentences that stand on their own - "
        "never split a single sentence across two scenes and never end or start a line with an ellipsis. "
        "Each clip is rendered independently, so a sentence left hanging mid-thought will sound abruptly cut off "
        "once the clips are stitched together, not continuous. Keep the dialogue within each clip's time budget by "
        "choosing how many whole sentences fit, not by truncating one. "
        "Do not restart with a new greeting or restate the topic in every scene. "
        "Never repeat a hook, sentence, example, or idea. Keep the title concise; do not copy the full topic or narration into it.\n\n"
        "You MUST produce an exact JSON object matching the exact scene timeline below. "
        "NEVER write dialogue longer than the STRICT max spoken words per scene.\n"
        f"Scene timeline slots:\n{slots_block}\n\n"
        "Return ONLY valid JSON with schema:\n"
        "{\n"
        '  "title": "string",\n'
        '  "objective": "string",\n'
        '  "opening_hook": "string",\n'
        '  "narration_summary": "string",\n'
        '  "scenes": [\n'
        "    {\n"
        '      "scene_index": 1,\n'
        '      "title": "short scene label",\n'
        '      "dialogue": "exact spoken lines fitting the duration",\n'
        '      "caption_text": "on-screen subtitle text",\n'
        '      "character_action": "subtle pose/gesture description",\n'
        '      "camera_motion": "Static eye-level medium shot",\n'
        '      "transition_type": "none"\n'
        "    }\n"
        "  ]\n"
        "}"
    )


def _literal_fallback_script(
    *,
    topic: str,
    language: str,
    windows: list[SceneWindow],
    character_names: dict[Optional[str], str],
    voice_mode: str,
) -> GeneratedScriptDraft:
    """Use the user's own text verbatim, chunked across scene slots.

    Unlike the educational template below, this never invents spoken lines,
    "explaining/presenting" gestures, or a topic narration - the user's own
    text IS the content. If voice is silent, each chunk becomes the visible
    action; otherwise each chunk is spoken (verbatim) as the scene's dialogue.
    """
    text = re.sub(r"\s+", " ", (topic or "").strip())
    is_vi = (language or "en-US").lower().startswith("vi") or bool(re.search(r"[À-ỹ]", text))
    chunks = _split_source_across_windows(text, windows, is_vi) if text else []
    titles = _source_scene_titles(len(windows), is_vi)
    is_silent = voice_mode == "silent"
    scenes: list[GeneratedSceneDraft] = []
    last_chunk = text
    for idx, w in enumerate(windows):
        chunk = chunks[idx] if idx < len(chunks) else ""
        char_name = character_names.get(w.character_id, "Subject")
        if is_silent:
            # Visual continuity, not speech: reusing the last described pose for a
            # trailing scene (when the source runs out) just holds the same action,
            # it doesn't sound like duplicated/repeated audio.
            effective = chunk or last_chunk
            last_chunk = effective
            dialogue = ""
            caption_text = ""
            action = f"{char_name}: {effective}" if char_name and char_name != "Subject" else effective
        else:
            # Chunks are already whole sentences (or empty) - never trim/repeat them,
            # since a cut-short or duplicated spoken line sounds obviously broken
            # once each scene's clip is rendered and stitched independently.
            dialogue = chunk
            caption_text = dialogue
            action = (
                "Natural delivery while speaking the line, subtle matching gestures."
                if dialogue
                else "Natural pause, continuing the scene calmly without speaking."
            )
        scenes.append(
            GeneratedSceneDraft(
                scene_index=w.scene_index,
                title=titles[idx] if idx < len(titles) else f"Scene {w.scene_index:02d}",
                dialogue=dialogue,
                caption_text=caption_text,
                character_action=action,
                camera_motion="Static eye-level medium shot",
                transition_type="none",
            )
        )
    return GeneratedScriptDraft(
        title=text[:100] or "Custom script",
        objective="",
        opening_hook=(scenes[0].dialogue or scenes[0].character_action) if scenes else "",
        narration_summary=" ".join(s.dialogue for s in scenes if s.dialogue),
        llm_provider_used="template_fallback_literal",
        scenes=scenes,
        raw_payload={"mode": "template_fallback_literal", "scene_count": len(scenes)},
    )


def _template_fallback_script(
    *,
    topic: str,
    topic_category: str,
    goal_description: str,
    language: str,
    windows: list[SceneWindow],
    character_names: dict[Optional[str], str],
    voice_mode: str = "ai_generated",
    script_mode: str = "ai_presenter",
) -> GeneratedScriptDraft:
    if script_mode == "literal":
        return _literal_fallback_script(
            topic=topic, language=language, windows=windows, character_names=character_names, voice_mode=voice_mode
        )

    is_vi = (language or "en-US").lower().startswith("vi")
    total_scenes = len(windows)
    scenes: list[GeneratedSceneDraft] = []
    topic_label = _fallback_topic_label(topic)
    spoken_topic_label = (
        topic_label
        if not is_vi or re.search(r"[À-ỹ]", topic)
        else "chủ đề này"
    )
    source_text = _fallback_source_text(topic, goal_description, is_vi)
    source_chunks = _split_source_across_windows(source_text, windows, is_vi) if source_text else []
    # _fallback_source_text already requires >= 8 real words before returning anything,
    # so any non-empty chunk here means we have genuine source material to use - even if
    # it only has enough complete sentences to fill some of the scenes (the rest are left
    # without dialogue rather than forced full by cutting a sentence in half).
    is_source_script = bool(source_chunks) and any(source_chunks)

    if is_source_script:
        titles = _source_scene_titles(total_scenes, is_vi)
        selected = list(zip(titles, source_chunks))
    else:
        # Generic educational outline used only when the inputs are too short
        # to form a narration. It deliberately avoids inventing domain-specific facts.
        if is_vi:
            beats = [
                ("Mở đầu", f"Để hiểu {spoken_topic_label}, trước tiên hãy làm rõ câu hỏi chính."),
                ("Khái niệm", "Sau đó, hãy diễn giải khái niệm cốt lõi bằng những từ ngữ đơn giản."),
                ("Bối cảnh", "Khi đã rõ khái niệm, tiếp theo cần xem xét bối cảnh liên quan."),
                ("Các bước", "Hãy đi lần lượt qua từng bước, không bỏ qua điều kiện quan trọng."),
                ("Ví dụ", "Hãy nối các bước đó với một ví dụ thực tế để thấy cách áp dụng."),
                ("Lưu ý", "Đồng thời kiểm tra giới hạn và những trường hợp kết quả có thể khác."),
                ("Áp dụng", "Khi điều kiện phù hợp, hãy áp dụng từng bước và theo dõi kết quả."),
                ("Rà soát", "Nếu thông tin chưa đủ, hãy dừng lại để kiểm tra thay vì kết luận vội."),
                ("Tổng kết", f"Tóm lại, hiểu {spoken_topic_label}, kiểm tra bối cảnh rồi mới áp dụng."),
            ]
        else:
            safe_label = "the topic" if re.search(r"[À-ỹ]", topic_label) else topic_label
            beats = [
                ("Opening", f"To understand {safe_label}, first clarify the main question."),
                ("Core idea", "Then explain the central concept in simple, direct language."),
                ("Context", "Once the idea is clear, consider the context that affects it."),
                ("Steps", "Follow each step in order without skipping an important condition."),
                ("Example", "Use one practical example to connect the steps with how they are applied."),
                ("Caution", "Check limitations and cases where the outcome may differ."),
                ("Apply", "When the conditions fit, follow the steps and observe the result."),
                ("Review", "If information is missing, pause and verify it instead of rushing to conclude."),
                ("Recap", f"In short, understand {safe_label}, check the context, then apply it."),
            ]
        selected = list(beats[:total_scenes])
        while len(selected) < total_scenes:
            part = len(selected) + 1
            extra_label = topic_label if is_vi else safe_label
            selected.append((f"Phần mở rộng {part}" if is_vi else f"Further point {part}", f"Ở phần này, đối chiếu lại {extra_label} với mục tiêu ban đầu và ghi nhận điều còn thiếu." if is_vi else f"Compare {extra_label} with the original goal and note what remains unclear."))

    for idx, (w, (beat_title, raw_line)) in enumerate(zip(windows, selected)):
        # Source-derived sentences are already whole and should never be truncated
        # here - a sentence cut short and patched with a fake "." sounds just as
        # abruptly cut off as the old ellipsis-splitting did. Only the generic
        # (short, hand-written) beat lines go through the safety-net trim.
        fitted = raw_line if is_source_script else trim_dialogue_to_fit(raw_line, w.duration_sec, language)
        char_name = character_names.get(w.character_id, "Presenter")
        action = (
            f"{char_name} looks directly at the camera with natural posture and subtle explanatory hand gestures."
            if idx % 2 == 0
            else f"{char_name} nods slightly while explaining {beat_title.lower()} with composed, steady hand movements."
        )
        scenes.append(
            GeneratedSceneDraft(
                scene_index=w.scene_index,
                title=beat_title,
                dialogue=fitted,
                caption_text=fitted,
                character_action=action,
                camera_motion="Static eye-level medium shot",
                transition_type="none",
            )
        )

    if voice_mode == "silent":
        for scene in scenes:
            scene.dialogue = ""
            scene.caption_text = ""

    title = _fallback_topic_label(topic)[:100]
    hook = scenes[0].dialogue if scenes else topic
    objective = goal_description.strip() if goal_description and len(goal_description.strip()) < 240 else (
        f"Giải thích nội dung về {topic_label} bằng tiếng Việt, theo trình tự rõ ràng."
        if is_vi else f"Explain {topic_label} clearly in {language}."
    )
    return GeneratedScriptDraft(
        title=title,
        objective=objective,
        opening_hook=hook,
        narration_summary=" ".join(s.dialogue for s in scenes),
        llm_provider_used="template_fallback",
        scenes=scenes,
        raw_payload={"mode": "template_fallback", "scene_count": len(scenes)},
    )


def _fallback_source_text(topic: str, goal_description: str, is_vi: bool) -> str:
    """Prefer supplied source material over fabricated, domain-specific filler."""
    topic_text = re.sub(r"\s+", " ", (topic or "").strip())
    goal_text = re.sub(r"\s+", " ", (goal_description or "").strip())
    selected = topic_text if len(topic_text.split()) >= 8 else goal_text
    if not selected:
        return ""
    has_vietnamese = bool(re.search(r"[À-ỹ]", selected))
    if is_vi != has_vietnamese:
        return ""
    return selected if len(selected.split()) >= 8 else ""


def _split_into_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation, keeping the punctuation attached."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_source_across_windows(text: str, windows: list[SceneWindow], is_vi: bool) -> list[str]:
    """Distribute the source text across scene windows by WHOLE sentences only.

    Each scene clip is rendered independently by the video provider, so a sentence
    split across two clips (e.g. with a trailing "...") never actually sounds
    continuous once stitched together - it just sounds like the speech was cut off.
    To avoid that, every window gets only complete sentences; if the source runs
    out of sentences before the windows run out, the remaining windows get no
    dialogue rather than an artificially truncated fragment.
    """
    sentences = _split_into_sentences(text)
    if not sentences or not windows:
        return []
    wps = 2.7 if is_vi else 2.4
    n = len(windows)
    chunks: list[list[str]] = [[] for _ in range(n)]
    s_idx = 0
    for w_idx, window in enumerate(windows):
        if s_idx >= len(sentences):
            break
        capacity = max(4, math.floor(window.duration_sec * wps))
        remaining_windows = n - w_idx
        remaining_sentences = len(sentences) - s_idx
        fair_share = max(1, remaining_sentences // remaining_windows)
        word_count = 0
        taken = 0
        while s_idx < len(sentences):
            sentence = sentences[s_idx]
            sentence_words = len(sentence.split())
            if taken > 0 and (word_count + sentence_words > capacity or taken >= fair_share):
                break
            chunks[w_idx].append(sentence)
            word_count += sentence_words
            s_idx += 1
            taken += 1
    return [" ".join(c).strip() for c in chunks]


def _source_scene_titles(count: int, is_vi: bool) -> list[str]:
    titles = (
        ["Mở đầu", "Ý tiếp theo", "Phân tích", "Tiếp tục", "Làm rõ", "Áp dụng", "Lưu ý", "Kết nối", "Tổng kết"]
        if is_vi
        else ["Opening", "Next point", "Analysis", "Continue", "Clarify", "Apply", "Caution", "Connect", "Recap"]
    )
    return [titles[i] if i < len(titles) else (f"Phần {i + 1}" if is_vi else f"Part {i + 1}") for i in range(count)]


def _fallback_topic_label(topic: str) -> str:
    """Reduce pasted briefs to a concise subject label for titles and dialogue."""
    text = re.sub(r"\s+", " ", (topic or "").strip())
    match = re.search(r"(?:tìm hiểu|phân tích|hướng dẫn|learn about|understand)\s+([^.!?;]{2,60})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()
    first_sentence = re.split(r"[.!?]\s+", text, maxsplit=1)[0]
    return first_sentence[:70].rstrip(" ,;:") or "the topic"


def generate_script_draft(
    *,
    runtime_cfg: EffectiveRuntimeConfig,
    topic: str,
    topic_category: str,
    goal_description: str,
    language: str,
    visual_style: str,
    windows: list[SceneWindow],
    character_names: dict[Optional[str], str],
    voice_mode: str = "ai_generated",
    script_mode: str = "ai_presenter",
) -> GeneratedScriptDraft:
    """Generate a structured multi-scene script using configured LLM provider or template fallback."""
    api_key = (runtime_cfg.script_llm_api_key or "").strip()
    if not api_key:
        return _template_fallback_script(
            topic=topic,
            topic_category=topic_category,
            goal_description=goal_description,
            language=language,
            windows=windows,
            character_names=character_names,
            voice_mode=voice_mode,
            script_mode=script_mode,
        )

    prompt = _build_llm_prompt(
        topic=topic,
        topic_category=topic_category,
        goal_description=goal_description,
        language=language,
        visual_style=visual_style,
        windows=windows,
        character_names=character_names,
        voice_mode=voice_mode,
        script_mode=script_mode,
    )

    try:
        provider = (runtime_cfg.script_llm_provider or "openai_compatible").lower()
        model = runtime_cfg.script_llm_model or "gpt-4o-mini"
        raw_text = ""

        if provider == "gemini":
            base = (runtime_cfg.script_llm_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            url = f"{base}/models/{model}:generateContent"
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            used_label = f"gemini:{model}"
        else:
            base = (runtime_cfg.script_llm_base_url or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Return valid JSON only."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.6,
                    },
                )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data["choices"][0]["message"]["content"]
            used_label = f"openai_compatible:{model}"

        parsed = _extract_json_block(raw_text)
        raw_scenes = parsed.get("scenes") or []
        by_idx = {int(s.get("scene_index", i + 1)): s for i, s in enumerate(raw_scenes)}
        is_silent = voice_mode == "silent"
        is_literal = script_mode == "literal"
        scenes: list[GeneratedSceneDraft] = []
        for w in windows:
            item = by_idx.get(w.scene_index) or (
                raw_scenes[w.scene_index - 1] if w.scene_index - 1 < len(raw_scenes) else {}
            )
            if is_silent:
                # Silent/cinematic mode never has spoken dialogue, regardless of what the
                # LLM returned - enforce it here rather than trusting the model's output.
                dialogue = ""
                caption_text = ""
                default_action = "Continue the described action naturally."
            else:
                dialogue = str(item.get("dialogue") or "").strip()
                est, warn = validate_scene_speech(dialogue, w.duration_sec, language)
                if warn:
                    dialogue = trim_dialogue_to_fit(dialogue, w.duration_sec, language)
                caption_text = str(item.get("caption_text") or dialogue)
                default_action = (
                    "Natural delivery while speaking the line, subtle matching gestures."
                    if is_literal
                    else "Natural presenter posture looking into camera with subtle hand gestures."
                )
            scenes.append(
                GeneratedSceneDraft(
                    scene_index=w.scene_index,
                    title=str(item.get("title") or f"Scene {w.scene_index:02d}"),
                    dialogue=dialogue,
                    caption_text=caption_text,
                    character_action=str(item.get("character_action") or default_action),
                    camera_motion=str(item.get("camera_motion") or "Static eye-level medium shot"),
                    transition_type=str(item.get("transition_type") or "none"),
                )
            )
        return GeneratedScriptDraft(
            title=str(parsed.get("title") or f"{topic} Video Script"),
            objective=str(parsed.get("objective") or goal_description),
            opening_hook=str(parsed.get("opening_hook") or (scenes[0].dialogue if scenes else "")),
            narration_summary=str(
                parsed.get("narration_summary") or " ".join(s.dialogue for s in scenes)
            ),
            llm_provider_used=used_label,
            scenes=scenes,
            raw_payload=parsed,
        )
    except Exception as exc:
        logger.warning("LLM script generation failed, falling back to structured template: %s", exc)
        fallback = _template_fallback_script(
            topic=topic,
            topic_category=topic_category,
            goal_description=goal_description,
            language=language,
            windows=windows,
            character_names=character_names,
            voice_mode=voice_mode,
            script_mode=script_mode,
        )
        fallback.raw_payload["llm_error"] = str(exc)
        return fallback


def compute_scene_speech_metrics(dialogue: str, duration_sec: float, language: str) -> tuple[float, Optional[str]]:
    est = estimate_speech_duration(dialogue, language)
    _, warning = validate_scene_speech(dialogue, duration_sec, language)
    return est, warning
