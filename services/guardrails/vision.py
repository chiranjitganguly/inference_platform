"""
Vision request validation and model capability registry for the Guardrails service.

This module enforces the multimodal image contract on POST /v1/chat/completions.
It is one of two validation modules (the other being function_calling.py) and runs
as a pre-proxy gate inside main.py before any request reaches LiteLLM.

Responsibilities:
  - Detect image parts in the request messages array.
  - Gate image requests to models that declare "vision" in their capabilities.
  - Validate each image part (format, MIME type, base64 integrity, size, detail).
  - Inject detail="auto" on image parts where the caller omitted the field.

All public functions are pure and stateless except VisionCapabilityCache.
"""
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
    """
    In-memory registry of model names that declare vision capability.

    Why needed: The guardrails service must reject image requests sent to
    non-vision models before they reach LiteLLM (FR-004 / spec 018). Rather than
    hard-coding model names, this cache loads the list dynamically from LiteLLM's
    /model/info endpoint at startup, so capability changes in config.yaml take
    effect on the next restart without touching guardrails code.

    Relationship to other components:
    - Populated by load(), called once during the FastAPI lifespan in main.py.
    - Stored as app.state.vision_cache and passed into validate_vision_request()
      on every multimodal request.
    - Mirrors FunctionCallingCapabilityCache in function_calling.py; both follow
      the same startup pattern but track different capability flags.

    Attributes:
        vision_model_names: Immutable set of model name strings where "vision"
            appears in model_info.capabilities in config.yaml. Empty until
            load() completes successfully.
    """

    vision_model_names: frozenset[str] = field(default_factory=frozenset)

    async def load(self) -> None:
        """
        Populate vision_model_names from LiteLLM's /model/info endpoint.

        Why needed: LiteLLM is the authoritative registry for model capabilities.
        Fetching from it at startup means capability declarations in config.yaml
        are automatically reflected without duplicating the list here.

        How it works: Calls GET {LITELLM_BASE_URL}/model/info with the master key,
        iterates the "data" array, and collects model_name values where "vision"
        appears in model_info.capabilities.

        Relationship to other functions:
        - Called once by _lifespan() in main.py before the app begins serving.
        - The populated vision_model_names set is consumed by
          validate_vision_request() on every multimodal request.

        Inputs: None (reads LITELLM_BASE_URL and LITELLM_MASTER_KEY from env).

        Returns: None. Mutates self.vision_model_names in place.

        Raises:
            RuntimeError: If the /model/info endpoint is unreachable or returns
                a non-2xx response. Treated as a fatal startup failure — the
                service will not start without a loaded registry.
        """
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
    """
    Return True if any message in the list carries at least one image_url content part.

    Why needed: Acts as the fast-path guard in _validate_vision() (main.py). If no
    image parts are found, the entire vision validation pipeline is skipped so that
    text-only requests incur zero overhead from this module.

    Relationship to other functions:
    - Called by _validate_vision() in main.py before entering validation logic.
    - count_image_parts() is its integer counterpart, used after validation passes
      to record the image count in the audit log.
    - inject_detail_defaults() is called only when this function returns True.

    Args:
        messages: The "messages" array from a parsed chat completion request body.
                  Each element may have a "content" field that is either a string
                  (text-only) or a list of typed parts.

    Returns:
        True if at least one message contains a content part with type="image_url".
        False if messages is empty, all content is plain strings, or no image_url
        parts exist.
    """
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def count_image_parts(messages: list[dict[str, Any]]) -> int:
    """
    Count the total number of image_url parts across all messages in the request.

    Why needed: Two uses — (1) validate_vision_request() uses this to enforce the
    per-request image count limit (MAX_IMAGES_PER_REQUEST env var, default 5);
    (2) _validate_vision() in main.py uses the count to populate the image_part_count
    field in the audit log, giving operators visibility into multimodal traffic
    without logging any image content.

    Relationship to other functions:
    - Called inside validate_vision_request() for the count-limit check.
    - Called by _validate_vision() in main.py after validation passes, to supply
      the count to _write_audit().

    Args:
        messages: The "messages" array from a parsed chat completion request body.

    Returns:
        Integer count of all image_url parts across all messages. Returns 0 if
        messages is empty or all content parts are non-image types.
    """
    count = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    count += 1
    return count


def inject_detail_defaults(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Set detail="auto" on every image_url part that omits the detail field.

    Why needed: The OpenAI API treats an absent detail field and detail="auto"
    identically at the provider level, but some providers behave inconsistently
    when the field is missing. Injecting the default ensures the forwarded request
    is always explicit. This also satisfies the spec clarification that the gateway
    is responsible for applying the default (not the caller and not LiteLLM).

    The three valid values are "low", "high", and "auto". "auto" delegates the
    cost/quality tradeoff to the upstream provider, which is the correct default
    for a multi-provider gateway.

    Relationship to other functions:
    - Called by _validate_vision() in main.py after validate_vision_request()
      returns None (i.e., validation passed). The modified messages are serialised
      back into the request body before forwarding to LiteLLM.
    - Only called when has_image_parts() returns True.

    Args:
        messages: The "messages" array from the request body. Modified in place:
                  each image_url part that lacks a "detail" key gets detail="auto"
                  added to its image_url sub-object.

    Returns:
        The same messages list, with detail="auto" injected where absent.
        Modifies the input in place and also returns it for convenience.
    """
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
    """
    Build a validation-rejection response in the OpenAI error envelope.

    Why needed: All vision validation rejections on /v1/chat/completions must use
    the OpenAI error envelope format (ADR-018) so OpenAI SDK clients can parse them
    natively without special-casing the platform. This helper centralises the
    envelope shape so it is not duplicated at each of the many call sites inside
    validate_vision_request().

    Relationship to other functions:
    - Called exclusively by validate_vision_request() for each validation failure.
    - _fc_error() in function_calling.py is its functional equivalent for
      function-calling rejections; both produce the same envelope shape.

    Args:
        message: Human-readable explanation of the rejection, surfaced to the caller.
        code:    Machine-readable error code (e.g. "vision_model_required").
                 Matches the codes in specs/018-multimodal-image-support/data-model.md.
        status:  HTTP status code (typically 400; 413 for image_too_large).

    Returns:
        A 2-tuple of (error_dict, http_status). The dict shape:
        {"error": {"message": ..., "type": "invalid_request_error", "code": ...}}.
    """
    return (
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status,
    )


def validate_vision_request(
    body: dict[str, Any],
    vision_models: frozenset[str],
) -> tuple[dict[str, Any], int] | None:
    """
    Validate a chat completion request body that contains image_url content parts.

    Why needed: LiteLLM forwards image content to providers without gateway-level
    validation. Without this function, a request with a non-vision model would fail
    at the provider with a confusing error; a base64 payload exceeding 5 MB would
    waste bandwidth and API credits; a malformed data URI would cause an opaque
    provider error. This function catches all of those cases early.

    Validation rules (applied in order; returns on first failure):
      1. stream + images → 400 vision_streaming_not_supported
         Streaming multimodal responses are out of scope for v1.
      2. Model not in vision_models → 400 vision_model_required
         Only models declaring "vision" in capabilities may receive image content.
      3. No text part alongside images → 400 missing_text_part
         All providers require at least one text part with image parts.
      4. Image count > MAX_IMAGES_PER_REQUEST → 400 image_count_exceeded
         Configurable limit (default 5) via MAX_IMAGES_PER_REQUEST env var.
      5. Per-image validation (for each image part):
         5a. Invalid "detail" value → 400 invalid_image_detail
         5b. Malformed data URI (wrong MIME type or bad base64) → 400 invalid_image_data_uri
         5c. Decoded base64 payload > 5 MB → 413 image_too_large
         5d. HTTP (non-TLS) URL → 400 invalid_image_url

    Relationship to other functions:
    - Called by _validate_vision() in main.py, which is the pre-proxy gate in the
      proxy() request handler.
    - Receives vision_models from VisionCapabilityCache (loaded at startup).
    - Uses _openai_error() for all error responses and count_image_parts() for the
      count limit check.
    - validate_tool_call_response() in function_calling.py is the post-proxy
      counterpart for function calling; this function has no response-side analogue.

    Args:
        body:          Parsed JSON request body. Must contain "messages" and "model".
        vision_models: Frozen set of model name strings that declare "vision"
                       capability, as loaded by VisionCapabilityCache.load().

    Returns:
        None if all checks pass (request is safe to forward to LiteLLM).
        A 2-tuple of (error_dict, http_status) on the first rule violation.
        The error_dict follows the OpenAI error envelope (ADR-018).
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
