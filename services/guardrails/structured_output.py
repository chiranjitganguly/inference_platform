"""
Structured output request/response validation and model capability registry.

This module is one of three validation modules for the Guardrails service (the others
being vision.py and function_calling.py). It enforces the structured output contract
on POST /v1/chat/completions when response_format.type is "json_schema":

  Pre-proxy  : validate_structured_output_request() — rejects invalid requests before
               they reach LiteLLM: streaming guard, missing fields, model capability
               gate, and JSON Schema draft-07 meta-schema validation.
  Post-proxy : validate_structured_output_response() — inspects the upstream response
               and signals "pass" or "retry". The retry loop lives in main.py.

Supporting helpers:
  has_structured_output_request() — fast-path detector (zero cost for non-SO requests)
  compute_schema_hash()           — 12-hex SHA-256 of the schema for metric attribution
  inject_schema_metadata()        — injects schema_name + schema_hash into request
                                    metadata so the arize_phoenix callback surfaces them
                                    as Phoenix span attributes

The module is consumed exclusively by main.py. All public functions are pure and
stateless except StructuredOutputCapabilityCache, which holds startup state.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import jsonschema
import jsonschema.exceptions

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


@dataclass
class StructuredOutputCapabilityCache:
    """
    In-memory registry of model names that declare json_schema capability.

    Splits models into two sets:
    - native_model_names: LiteLLM passes response_format directly to the provider API
      (OpenAI and Anthropic providers).
    - prompt_model_names: LiteLLM adds a system-prompt instruction to enforce JSON
      output (Google and Cohere providers).

    all_json_schema_models is the union — used by the capability gate in
    validate_structured_output_request().

    Relationship to other components:
    - Populated by load(), called once during the FastAPI lifespan in main.py.
    - Stored as app.state.so_cache so every request handler can access it without
      a per-request HTTP call.
    - Passed into validate_structured_output_request() as cache.
    - Mirrors FunctionCallingCapabilityCache in function_calling.py; same startup
      pattern, different capability flag ("json_schema").
    """

    native_model_names: frozenset[str] = field(default_factory=frozenset)
    prompt_model_names: frozenset[str] = field(default_factory=frozenset)
    all_json_schema_models: frozenset[str] = field(default_factory=frozenset)

    async def load(self) -> None:
        """
        Populate capability sets from LiteLLM's /model/info endpoint.

        Models with "json_schema" in capabilities are collected. Provider determines
        set membership: openai/anthropic → native_model_names; all others →
        prompt_model_names.

        Raises RuntimeError if the endpoint is unreachable (fail-fast at startup).
        """
        url = f"{LITELLM_BASE_URL}/model/info"
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"StructuredOutputCapabilityCache: failed to load model info "
                    f"from {url}: {exc}"
                ) from exc

        data = resp.json()
        models: list[dict[str, Any]] = data.get("data", [])
        native: set[str] = set()
        prompt: set[str] = set()
        for entry in models:
            info: dict[str, Any] = entry.get("model_info", {})
            # LiteLLM does not surface our custom "json_schema" capability string
            # in the capabilities array; use supports_response_schema instead —
            # that is the authoritative flag LiteLLM sets for structured output.
            if not info.get("supports_response_schema"):
                continue
            name: str = entry.get("model_name", "")
            provider: str = info.get("provider", "")
            if provider in ("openai", "anthropic"):
                native.add(name)
            else:
                prompt.add(name)
        self.native_model_names = frozenset(native)
        self.prompt_model_names = frozenset(prompt)
        self.all_json_schema_models = frozenset(native | prompt)


def has_structured_output_request(body: dict[str, Any]) -> bool:
    """
    Return True if the request body activates structured output mode.

    Structured output mode is activated when response_format.type equals "json_schema".
    This is the fast-path guard — if False, the entire SO pipeline is skipped with
    zero additional cost for ordinary text requests.
    """
    rf = body.get("response_format")
    return isinstance(rf, dict) and rf.get("type") == "json_schema"


def compute_schema_hash(schema: dict[str, Any]) -> str:
    """
    Return a 12-hex-character SHA-256 digest of the schema for metric attribution.

    The hash is deterministic (sorted key serialisation) so identical schemas always
    produce the same hash. It is used as a Prometheus label and audit log field,
    never as a cache key. Schema content never appears in logs — only this hash.
    """
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()[:12]


def inject_schema_metadata(
    body: dict[str, Any], schema_name: str, schema_hash: str
) -> dict[str, Any]:
    """
    Inject schema_name and schema_hash into the LiteLLM request metadata dict.

    LiteLLM's arize_phoenix callback propagates metadata fields as custom attributes
    on the Phoenix LLM span. This is the same mechanism used by _inject_no_log() for
    embeddings. The result is that every structured output request appears in Phoenix
    with a span attribute keyed on the caller-provided schema name.

    A shallow copy of body is returned; the original is not mutated.
    """
    body = dict(body)
    metadata: dict[str, Any] = dict(body.get("metadata") or {})
    metadata["schema_name"] = schema_name
    metadata["schema_hash"] = schema_hash
    body["metadata"] = metadata
    return body


def _so_error(message: str, code: str, status: int) -> tuple[dict[str, Any], int]:
    """Build a structured validation-rejection response in the OpenAI error envelope."""
    return (
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status,
    )


def validate_structured_output_request(
    body: dict[str, Any],
    cache: StructuredOutputCapabilityCache,
) -> tuple[dict[str, Any], int] | None:
    """
    Validate a chat completion request body that activates structured output mode.

    Applied in order; returns on the first failure. All failures use the OpenAI
    error envelope (ADR-018).

    Validation rules:
      1. stream: true → 400 structured_output_streaming_not_supported
         Streaming with SO is out of scope; the full response is needed for validation.
      2. response_format.name absent/empty → 400 invalid_structured_output_request
         The name is required as the Phoenix span attribute and error response field.
      3. response_format.schema absent/non-dict → 400 invalid_structured_output_request
         Without a schema there is nothing to validate against.
      4. Model not in cache.all_json_schema_models → 400 structured_output_model_required
         Only models declaring json_schema capability may use this mode.
      5. Schema fails draft-07 meta-schema → 422 invalid_json_schema
         Reject before inference so no credits are consumed for an unsatisfiable schema.

    Returns:
        None if all checks pass (request is safe to forward).
        A 2-tuple of (error_dict, http_status) on the first rule violation.
    """
    rf: dict[str, Any] = body.get("response_format") or {}

    # 1. Streaming guard
    if body.get("stream") is True:
        return _so_error(
            "Streaming is not supported when response_format.type is json_schema.",
            "structured_output_streaming_not_supported",
            400,
        )

    # 2. name required
    name = rf.get("name")
    if not name or not isinstance(name, str):
        return _so_error(
            "response_format.name is required and must be a non-empty string.",
            "invalid_structured_output_request",
            400,
        )

    # 3. schema required
    schema = rf.get("schema")
    if not isinstance(schema, dict):
        return _so_error(
            "response_format.schema is required and must be a JSON object.",
            "invalid_structured_output_request",
            400,
        )

    # 4. model capability gate
    model: str = body.get("model", "")
    if model not in cache.all_json_schema_models:
        supported = ", ".join(sorted(cache.all_json_schema_models))
        return _so_error(
            f"Model '{model}' does not support structured output (json_schema mode). "
            f"Supported models: {supported}.",
            "structured_output_model_required",
            400,
        )

    # 5. schema self-validity (draft-07 meta-schema)
    try:
        jsonschema.Draft7Validator.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        return (
            {
                "error": {
                    "message": (
                        f"The provided schema is not a valid JSON Schema: {exc.message}"
                    ),
                    "type": "invalid_json_schema",
                    "code": "invalid_json_schema",
                }
            },
            422,
        )

    return None


def validate_structured_output_response(
    response_body: dict[str, Any],
    schema: dict[str, Any],
) -> Literal["pass", "retry"]:
    """
    Inspect the upstream response to determine if choices[0].message.content conforms.

    This is the post-proxy counterpart to validate_structured_output_request().
    It is called inside the retry loop in main.py after every LiteLLM 200 response.

    Returns "pass" if:
      - choices[0].message.content is a string
      - It parses as JSON
      - The parsed object validates against schema

    Returns "retry" in all other cases:
      - choices is absent, empty, or malformed
      - content is None or not a string
      - content is not valid JSON
      - content parses but fails jsonschema.validate()

    The caller (main.py retry loop) handles retry budget exhaustion and the final
    422 schema_conformance_failure response — this function only signals pass/retry.
    """
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return "retry"

    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str):
        return "retry"

    try:
        parsed: Any = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return "retry"

    try:
        jsonschema.validate(parsed, schema)
    except jsonschema.ValidationError:
        return "retry"

    return "pass"
