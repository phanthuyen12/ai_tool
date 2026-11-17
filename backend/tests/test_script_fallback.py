from app.services.ai_script.llm_generator import (
    _build_llm_prompt,
    _split_source_across_windows,
    _template_fallback_script,
)
from app.services.ai_script.scene_planner import SceneWindow


def _windows(count: int, duration: float = 8.0) -> list[SceneWindow]:
    return [
        SceneWindow(
            scene_index=i + 1,
            start_time_sec=i * duration,
            end_time_sec=(i + 1) * duration,
            duration_sec=duration,
            character_id=None,
            background_id=None,
            is_master_scene=(i == 0),
        )
        for i in range(count)
    ]


def test_vietnamese_long_brief_is_split_into_whole_sentences_without_ellipsis():
    draft = _template_fallback_script(
        topic=(
            "Bạn muốn giao dịch theo dòng tiền lớn? Hãy tìm hiểu SMC! Đầu tiên, xác định cấu trúc thị trường: "
            "đỉnh cao hơn, đáy cao hơn là xu hướng tăng. Tiếp theo, tìm Liquidity, nơi tập trung nhiều Stop Loss. "
            "Khi giá quét thanh khoản, đừng vội vào lệnh! Hãy chờ CHoCH xác nhận thay đổi cấu trúc, sau đó đợi giá "
            "hồi về Order Block để tìm điểm Buy hoặc Sell. Luôn đặt Stop Loss và quản lý vốn. Nhớ nhé, SMC giúp "
            "bạn đọc hành vi giá, không đảm bảo thắng mọi lệnh!"
        ),
        topic_category="education",
        goal_description="A long pasted brief that repeats all of the topic and should not become the script objective.",
        language="vi-VN",
        windows=_windows(9),
        character_names={},
    )

    dialogue = [scene.dialogue for scene in draft.scenes]
    non_empty = [d for d in dialogue if d]
    # Only 8 complete sentences exist in the source, so the 9th scene is left
    # without dialogue rather than manufacturing a cut-off fragment for it.
    assert len(non_empty) == len(set(non_empty)) == 8
    assert dialogue[-1] == ""
    flattened = " ".join(dialogue)
    assert "..." not in flattened
    assert "Bạn muốn giao dịch" in flattened
    assert "CHoCH" in flattened
    assert "Order Block" in flattened
    assert len(draft.title) < 100
    assert "SMC giúp ta đọc hành vi giá" not in flattened


def test_long_sentence_is_never_split_across_scene_boundary():
    chunks = _split_source_across_windows(
        "Để hiểu nội dung này, hãy lần lượt quan sát bối cảnh, xác định từng dấu hiệu quan trọng, "
        "so sánh các dữ kiện liên quan và chỉ sau đó mới đưa ra kết luận phù hợp với mục tiêu.",
        _windows(2),
        True,
    )
    assert "..." not in chunks[0]
    assert "..." not in chunks[1]
    assert chunks[0].endswith("mục tiêu.")
    assert chunks[1] == ""


def test_english_fallback_does_not_embed_vietnamese_topic_text():
    draft = _template_fallback_script(
        topic="Bạn muốn giao dịch theo dòng tiền lớn? Hãy tìm hiểu SMC!",
        topic_category="education",
        goal_description="",
        language="en-US",
        windows=_windows(8),
        character_names={},
    )
    assert all("Bạn muốn" not in scene.dialogue for scene in draft.scenes)
    assert len({scene.dialogue for scene in draft.scenes}) == 8


def test_long_brief_fallback_uses_source_without_forex_specific_invention():
    source = (
        "Giấc ngủ đủ giúp cơ thể phục hồi. Hãy duy trì giờ ngủ ổn định, giảm ánh sáng xanh trước khi ngủ, "
        "giữ phòng yên tĩnh và tránh dùng caffeine vào buổi tối. Nếu mất ngủ kéo dài, hãy trao đổi với chuyên gia y tế."
    )
    draft = _template_fallback_script(
        topic=source,
        topic_category="health",
        goal_description="",
        language="vi-VN",
        windows=_windows(4),
        character_names={},
    )
    script_text = " ".join(scene.dialogue for scene in draft.scenes)
    assert "giấc ngủ" in script_text.lower()
    assert "caffeine" in script_text.lower()
    assert "Stop Loss" not in script_text


def test_source_sentence_longer_than_one_scene_budget_is_kept_whole_not_truncated():
    # This sentence is long enough that trim_dialogue_to_fit would normally cut it
    # (and silently patch a fake "." on the cut point) if applied to source-derived
    # dialogue - it must come through completely intact instead.
    source = (
        "Hãy duy trì giờ ngủ ổn định, giảm ánh sáng xanh trước khi ngủ, giữ phòng yên tĩnh và tránh dùng "
        "caffeine vào buổi tối để cơ thể dễ dàng đi vào giấc ngủ sâu hơn."
    )
    draft = _template_fallback_script(
        topic=source,
        topic_category="health",
        goal_description="",
        language="vi-VN",
        windows=_windows(1, duration=8.0),
        character_names={},
    )
    assert draft.scenes[0].dialogue == source


def test_llm_prompt_requires_continuity_and_non_repetition():
    prompt = _build_llm_prompt(
        topic="SMC",
        topic_category="education",
        goal_description="",
        language="vi-VN",
        visual_style="photorealistic",
        windows=_windows(2),
        character_names={},
    )
    assert "NARRATIVE CONTINUITY RULE" in prompt
    assert "Never repeat" in prompt


def test_literal_silent_mode_fallback_never_invents_dialogue_or_presenter_gestures():
    draft = _template_fallback_script(
        topic=(
            "Nguoi dan ong met moi, ngap lien tuc, mat lim dim, dau gat gu roi giat minh tinh lai. "
            "Vai giay sau, anh khong chiu noi con buon ngu, tu tu guc dau xuong ban va ngu say."
        ),
        topic_category="lifestyle",
        goal_description="",
        language="vi-VN",
        windows=_windows(5),
        character_names={},
        voice_mode="silent",
        script_mode="literal",
    )
    assert all(scene.dialogue == "" for scene in draft.scenes)
    assert all(scene.caption_text == "" for scene in draft.scenes)
    joined_actions = " ".join(scene.character_action for scene in draft.scenes)
    assert "explain" not in joined_actions.lower()
    assert "guc dau" in joined_actions.lower() or "ngu say" in joined_actions.lower()


def test_literal_mode_with_voice_speaks_the_exact_text_verbatim():
    draft = _template_fallback_script(
        topic="Toi ten la An. Hom nay toi se huong dan ban cach pha ca phe sua da ngon.",
        topic_category="lifestyle",
        goal_description="",
        language="vi-VN",
        windows=_windows(2),
        character_names={},
        voice_mode="ai_generated",
        script_mode="literal",
    )
    joined_dialogue = " ".join(scene.dialogue for scene in draft.scenes).lower()
    assert "toi ten la an" in joined_dialogue
    assert "ca phe sua da" in joined_dialogue
    assert all("explain" not in scene.character_action.lower() for scene in draft.scenes)


def test_literal_silent_llm_prompt_forbids_dialogue_and_presenter_framing():
    prompt = _build_llm_prompt(
        topic="A tired man slowly falls asleep at his desk.",
        topic_category="lifestyle",
        goal_description="",
        language="en-US",
        visual_style="photorealistic",
        windows=_windows(3),
        character_names={},
        voice_mode="silent",
        script_mode="literal",
    )
    assert "no spoken dialogue anywhere" in prompt.lower()
    assert "AI presenter videos" not in prompt


def test_literal_voiced_llm_prompt_forbids_invented_dialogue():
    prompt = _build_llm_prompt(
        topic="My exact line one. My exact line two.",
        topic_category="lifestyle",
        goal_description="",
        language="en-US",
        visual_style="photorealistic",
        windows=_windows(2),
        character_names={},
        voice_mode="ai_generated",
        script_mode="literal",
    )
    assert "do not invent" in prompt.lower()
    assert "AI presenter videos" not in prompt


def test_ai_presenter_mode_is_unaffected_by_script_mode_default():
    prompt = _build_llm_prompt(
        topic="SMC",
        topic_category="education",
        goal_description="",
        language="vi-VN",
        visual_style="photorealistic",
        windows=_windows(2),
        character_names={},
    )
    assert "AI presenter videos" in prompt
