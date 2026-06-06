"""Vision request validation and model capability registry for the Guardrails service."""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
MAX_IMAGES_PER_REQUEST = int(os.environ.get("MAX_IMAGES_PER_REQUEST", "5"))
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

_ALLOWED_DETAIL_VALUES: frozenset[str] = frozenset({"low", "high", "auto"})
_ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)
_BASE64_DATA_URI_RE = re.compile(
    r"^data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/]+=*)$"
)
_HTTPS_URL_RE = re.compile(r"^https://", re.IGNORECASE)


@dataclass
class VisionCapabilityCache:
    """In-memory registry of model names that declare vision capability."""

    vision_model_names: frozenset[str] = field(default_factory=frozenset)

    async def load(self) -> None:
        """Populate the registry from LiteLLM /model/info. Raises RuntimeError if unreachable."""
        url = f"{LITELLM_BASE_URL}/model/info"
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"VisionCapabilityCache: failed to load model info from {url}: {exc}"
                ) from exc

        data = resp.json()
        # LiteLLM /model/info returns {"data": [{"model_name": ..., "model_info": {...}}, ...]}
        models = data.get("data", [])
        vision_names: set[str] = set()
        for entry in models:
            info = entry.get("model_info", {})
            capabilities: list[str] = info.get("capabilities", [])
            if "vision" in capabilities:
                vision_names.add(entry.get("model_name", ""))
        self.vision_model_names = frozenset(vision_names)


def has_image_parts(messages: list[dict[str, Any]]) -> bool:
    """Return True if any message carries at least one image_url content part."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def count_image_parts(messages: list[dict[str, Any]]) -> int:
    """Count total image_url parts across all messages."""
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    count += 1
    return count


def inject_detail_defaults(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Set detail='auto' on every image_url part that omits the detail field."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image_url = part.get("image_url", {})
                    if isinstance(image_url, dict) and "detail" not in image_url:
                        image_url["detail"] = "auto"
    return messages


def _openai_error(message: str, code: str, status: int) -> tuple[dict[str, Any], int]:
    """Return (OpenAI-envelope error dict, HTTP status) for a vision validation rejection."""
    return (
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status,
    )


def validate_vision_request(
    body: dict[str, Any],
    vision_models: frozenset[str],
) -> tuple[dict[str, Any], int] | None:
    """
    Validate a multimodal chat completion request body.

    Returns None on success, or (error_dict, http_status) on the first validation failure.
    Checks are applied in the order defined by the data-model state-transition diagram.
    """
    messages: list[dict[str, Any]] = body.get("messages", [])

    # 1. Streaming + image is not supported in v1.
    if body.get("stream") is True:
        return _openai_error(
            "Streaming is not supported for multimodal requests. Set 'stream' to false or omit it.",
            "vision_streaming_not_supported",
            400,
        )

    # 2. Requested model must be vision-capable.
    model: str = body.get("model", "")
    if model and model not in vision_models:
        vision_list = ", ".join(sorted(vision_models))
        return _openai_error(
            f"Model '{model}' does not support image inputs. "
            f"Use a vision-capable model: {vision_list}.",
            "vision_model_required",
            400,
        )
    if not model:
        # No model specified — cannot guarantee a vision-capable model will be selected.
        return _openai_error(
            "A model must be specified for multimodal requests. "
            f"Use a vision-capable model: {', '.join(sorted(vision_models))}.",
            "vision_model_required",
            400,
        )

    # 3. At least one text part must accompany the image parts.
    def _iter_parts(msg: dict[str, Any]) -> list[Any]:
        c = msg.get("content")
        return c if isinstance(c, list) else []

    has_text = any(
        isinstance(part, dict) and part.get("type") == "text"
        for msg in messages
        for part in _iter_parts(msg)
    )
    if not has_text:
        return _openai_error(
            "Multimodal messages must contain at least one text part alongside image parts.",
            "missing_text_part",
            400,
        )

    # 4. Image count limit.
    image_count = count_image_parts(messages)
    if image_count > MAX_IMAGES_PER_REQUEST:
        return _openai_error(
            f"Request contains {image_count} images; maximum allowed is {MAX_IMAGES_PER_REQUEST}.",
            "image_count_exceeded",
            400,
        )

    # 5. Per-image validation.
    part_index = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue

            image_url_obj = part.get("image_url", {})
            if not isinstance(image_url_obj, dict):
                return _openai_error(
                    f"Image part at index {part_index} has an invalid 'image_url' field.",
                    "invalid_image_data_uri",
                    400,
                )
            url: str = image_url_obj.get("url", "")
            detail = image_url_obj.get("detail")

            # 5a. Validate detail value if present.
            if detail is not None and detail not in _ALLOWED_DETAIL_VALUES:
                return _openai_error(
                    f"Image part at index {part_index} has an invalid 'detail' value '{detail}'. "
                    f"Allowed values: {', '.join(sorted(_ALLOWED_DETAIL_VALUES))}.",
                    "invalid_image_detail",
                    400,
                )

            if url.startswith("data:"):
                # 5b. Base64 data URI validation.
                m = _BASE64_DATA_URI_RE.match(url)
                if not m:
                    return _openai_error(
                        f"Image part at index {part_index} has a malformed data URI. "
                        "Expected format: data:image/<jpeg|png|gif|webp>;base64,<data>.",
                        "invalid_image_data_uri",
                        400,
                    )
                b64_payload = m.group(2)
                try:
                    decoded = base64.b64decode(b64_payload, validate=True)
                except Exception:
                    return _openai_error(
                        f"Image part at index {part_index} contains invalid base64 data.",
                        "invalid_image_data_uri",
                        400,
                    )
                # 5c. Size limit: 5 MB.
                if len(decoded) > MAX_IMAGE_BYTES:
                    size_mb = len(decoded) / (1024 * 1024)
                    return (
                        {
                            "error": {
                                "message": f"Image part at index {part_index} exceeds the 5 MB limit "
                                f"({size_mb:.1f} MB).",
                                "type": "invalid_request_error",
                                "code": "image_too_large",
                            }
                        },
                        413,
                    )
            elif not _HTTPS_URL_RE.match(url):
                # 5d. URL images must use HTTPS.
                return _openai_error(
                    f"Image part at index {part_index} has an invalid URL. "
                    "Only https:// URLs and data: URIs are accepted.",
                    "invalid_image_url",
                    400,
                )

            part_index += 1

    return None
