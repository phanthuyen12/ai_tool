import pytest

from app.services.flow2api.parser import (
    Flow2APIUpstreamError,
    classify_flow2api_error,
    classify_model_entry,
    parse_image_url_from_content,
    parse_video_from_content,
)


def test_classify_model_entry():
    img = classify_model_entry(
        {
            "id": "gemini-3.1-flash-image-portrait-2k",
            "type": "image",
            "output_modalities": ["image"],
        }
    )
    vid = classify_model_entry(
        {
            "id": "omni-1.2-flash-8s-portrait",
            "output_modalities": ["video"],
        }
    )
    assert img["type"] == "image"
    assert vid["type"] == "video"


def test_parse_image_url_markdown():
    url = parse_image_url_from_content(
        "Here is your image:\n![Generated Image](https://flow.aetrading.vn/tmp/abc123.png)"
    )
    assert url == "https://flow.aetrading.vn/tmp/abc123.png"

    with pytest.raises(Flow2APIUpstreamError):
        parse_image_url_from_content("错误: upstream generation failed")


def test_parse_video_single_and_double_quotes_and_media_id():
    single = "```html\n<video src='https://flow.aetrading.vn/tmp/v1.mp4' data-media-id='mid-999' controls></video>\n```"
    url1, mid1 = parse_video_from_content(single)
    assert url1 == "https://flow.aetrading.vn/tmp/v1.mp4"
    assert mid1 == "mid-999"

    double = '<video src="https://flow.aetrading.vn/tmp/v2.mp4"></video>'
    url2, mid2 = parse_video_from_content(double)
    assert url2 == "https://flow.aetrading.vn/tmp/v2.mp4"
    assert mid2 is None


def test_classify_all_documented_flow2api_errors():
    d_audio = classify_flow2api_error(
        status_code=500,
        message="视频生成失败: Flow 上游拒绝视频生成：PUBLIC_ERROR_AUDIO_FILTERED，请重试",
    )
    assert d_audio.error_code == "PUBLIC_ERROR_AUDIO_FILTERED"
    assert d_audio.retryable is True
    assert d_audio.max_retries == 2
    assert d_audio.adjust_audio_prompt is True

    for fatal_code in (
        "PUBLIC_ERROR_UNSAFE_GENERATION",
        "PUBLIC_ERROR_MODEL_ACCESS_DENIED",
        "PUBLIC_ERROR_USER_QUOTA_REACHED",
        "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED",
    ):
        d_fatal = classify_flow2api_error(status_code=400, message=f"Upstream rejected: {fatal_code}")
        assert d_fatal.error_code == fatal_code
        assert d_fatal.retryable is False
        assert d_fatal.max_retries == 0

    d_unusual = classify_flow2api_error(status_code=429, message="PUBLIC_ERROR_UNUSUAL_ACTIVITY")
    assert d_unusual.retryable is True
    assert d_unusual.max_retries == 1

    d_captcha = classify_flow2api_error(status_code=500, message="Failed to obtain reCAPTCHA token")
    assert d_captcha.error_code == "RECAPTCHA_TOKEN_FAILED"
    assert d_captcha.retryable is True
    assert d_captcha.max_retries == 2

    assert classify_flow2api_error(status_code=401, message="Unauthorized").retryable is False
    assert classify_flow2api_error(status_code=429, message="Rate limit").retryable is True
    assert classify_flow2api_error(status_code=504, message="Gateway timeout").retryable is True

