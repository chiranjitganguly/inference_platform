"""
Contract tests for POST /v1/chat/completions — multimodal image support (feature 018).

Tests are organised by acceptance criterion from specs/018-multimodal-image-support/plan.md:
  AC-1  URL image → HTTP 200 with non-empty content          (US1)
  AC-2  Base64 image → HTTP 200 with non-empty content       (US2)
  AC-3  Non-vision model + image → 400 vision_model_required (US3)
  AC-4  stream:true + image → 400 vision_streaming_not_supported
  AC-5  Malformed base64 URI → 400 invalid_image_data_uri    (US2)
  AC-6  Image count exceeded → 400 image_count_exceeded      (US4)
  AC-7  Missing text part → 400 missing_text_part            (US2)
  AC-8  Text-only regression → HTTP 200                      (regression)

All validation rejections use the OpenAI error envelope per ADR-018:
  {"error": {"message": "...", "type": "invalid_request_error", "code": "..."}}
"""
from __future__ import annotations

import base64
import urllib.request

import pytest
import requests

# ── Helpers ───────────────────────────────────────────────────────────────────

_VISION_IMAGE_URL = (
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/"
    "PNG_transparency_demonstration_1.png/240px-PNG_transparency_demonstration_1.png"
)

_TINY_PNG_B64 = (
    # 1×1 transparent PNG, base64-encoded — avoids any network fetch in AC-2/AC-5
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _vision_url_message(text: str, url: str = _VISION_IMAGE_URL) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


def _vision_b64_message(text: str, b64: str = _TINY_PNG_B64, detail: str | None = None) -> dict:
    image_url_obj: dict = {"url": f"data:image/png;base64,{b64}"}
    if detail is not None:
        image_url_obj["detail"] = detail
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": image_url_obj},
        ],
    }


def _assert_openai_error(body: dict, expected_code: str) -> None:
    """Assert the response body uses the OpenAI error envelope (ADR-018)."""
    error = body.get("error", {})
    assert isinstance(error, dict), (
        f"Expected 'error' to be a dict (OpenAI envelope), got {type(error).__name__}"
    )
    assert error.get("type") == "invalid_request_error", (
        f"Expected error.type='invalid_request_error', got '{error.get('type')}'"
    )
    assert error.get("code") == expected_code, (
        f"Expected error.code='{expected_code}', got '{error.get('code')}'"
    )
    assert isinstance(error.get("message"), str) and len(error["message"]) > 0, (
        "error.message must be a non-empty string"
    )


# ── AC-1: URL image → HTTP 200 ────────────────────────────────────────────────


def test_vision_url_image_returns_200(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-1: A URL-referenced image alongside a text prompt returns HTTP 200 with content."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [_vision_url_message("What colour is dominant in this image?")],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "choices[0].message.content must be a non-empty string describing the image"
    )


# ── AC-2: Base64 image → HTTP 200 ────────────────────────────────────────────


def test_vision_base64_image_returns_200(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-2: A base64 data URI image alongside a text prompt returns HTTP 200 with content."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [_vision_b64_message("Describe this image.", detail="low")],
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    body = resp.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "choices[0].message.content must be a non-empty string"
    )


def test_vision_base64_detail_auto_injected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-2 variant: omitting detail is accepted; gateway injects detail='auto'."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [_vision_b64_message("What is in this image?")],
            # No detail field — gateway must inject "auto"
        },
        headers=auth_headers,
        timeout=45,
    )
    assert resp.status_code == 200, (
        f"Omitting 'detail' should be accepted (default auto), got {resp.status_code}: {resp.text}"
    )


# ── AC-3: Non-vision model → 400 vision_model_required ───────────────────────


def test_non_vision_model_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-3: A non-vision model with image content returns 400 vision_model_required."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "command-r-plus",
            "messages": [_vision_url_message("Describe this.")],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    _assert_openai_error(body, "vision_model_required")
    # The error message must name the rejected model.
    assert "command-r-plus" in body["error"]["message"], (
        "Error message must name the rejected model 'command-r-plus'"
    )


def test_embedding_model_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-3 variant: embedding model also rejected for vision requests."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "text-embedding-3-small",
            "messages": [_vision_url_message("Describe this.")],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "vision_model_required")


# ── AC-4: stream:true + image → 400 vision_streaming_not_supported ───────────


def test_streaming_with_image_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-4: stream=true alongside image content returns 400 vision_streaming_not_supported."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "stream": True,
            "messages": [_vision_url_message("Describe.")],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "vision_streaming_not_supported")


# ── AC-5: Malformed base64 → 400 invalid_image_data_uri ──────────────────────


def test_malformed_base64_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-5: A malformed base64 data URI returns 400 invalid_image_data_uri."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,!!!not-valid-base64!!!"}},
                ],
            }],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "invalid_image_data_uri")


def test_unsupported_mime_type_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-5 variant: unsupported MIME type (image/tiff) returns 400 invalid_image_data_uri."""
    tiff_b64 = base64.b64encode(b"fake tiff content").decode()
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/tiff;base64,{tiff_b64}"}},
                ],
            }],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400 for unsupported MIME, got {resp.status_code}"
    _assert_openai_error(resp.json(), "invalid_image_data_uri")


# ── AC-6: Image count exceeded → 400 image_count_exceeded ────────────────────


def test_image_count_exceeded(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-6: More than 5 image parts returns 400 image_count_exceeded."""
    content: list = [{"type": "text", "text": "Describe all these images."}]
    for _ in range(6):
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"}})

    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": content}],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    _assert_openai_error(body, "image_count_exceeded")


# ── AC-7: Missing text part → 400 missing_text_part ──────────────────────────


def test_missing_text_part_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-7: An image-only content array (no text part) returns 400 missing_text_part."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"}},
                ],
            }],
        },
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    _assert_openai_error(resp.json(), "missing_text_part")


# ── AC-8: Text-only regression ────────────────────────────────────────────────


def test_text_only_regression(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """AC-8: Text-only chat completion is unaffected by vision validation (regression guard)."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is 2 + 2?"}],
        },
        headers=auth_headers,
        timeout=30,
    )
    assert resp.status_code == 200, (
        f"Text-only request must still return 200 after vision feature added. "
        f"Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "Text-only response must still have non-empty content"
    )
