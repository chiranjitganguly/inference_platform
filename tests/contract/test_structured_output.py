"""
Contract tests for POST /v1/chat/completions — structured JSON output (feature 020).

Tests cover all acceptance criteria from specs/020-structured-json-output/plan.md:

  SC-001  10 consecutive structured output calls all return schema-conforming JSON  (US1)
  SC-002  Invalid schema rejected before inference (< 100 ms, no model call)         (US2)
  SC-003  Schema conformance failure has a distinct error.type                        (US3)
  FR-008  stream:true + json_schema → 400                                             (US1)
  FR-001  Model without json_schema capability → 400                                 (US2)
  FR-001  Missing response_format.name → 400                                         (US2)
  FR-001  Missing response_format.schema → 400                                       (US2)
  FR-008  Request without response_format is unaffected                              (regression)

Unit tests for structured_output.py helpers (no stack required):
  validate_structured_output_request — all 5 rules
  validate_structured_output_response — pass / retry
  compute_schema_hash — determinism
  inject_schema_metadata — idempotence

All validation rejections use the OpenAI error envelope per ADR-018.
"""
from __future__ import annotations

import json
import unittest.mock as mock

import jsonschema
import pytest
import requests

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string"},
        "total": {"type": "number"},
    },
    "required": ["invoice_number", "total"],
    "additionalProperties": False,
}

_FRUIT_SCHEMA = {
    "type": "object",
    "properties": {
        "fruits": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fruits"],
    "additionalProperties": False,
}


def _so_request(
    model: str = "gpt-4o-mini",
    schema: dict | None = None,
    name: str = "invoice_schema",
    strict: bool = True,
    extra: dict | None = None,
) -> dict:
    """Build a structured output chat completion request body."""
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Extract: Invoice INV-001 total $99.99"}],
        "response_format": {
            "type": "json_schema",
            "name": name,
            "strict": strict,
            "schema": schema if schema is not None else _INVOICE_SCHEMA,
        },
    }
    if extra:
        body.update(extra)
    return body


def _assert_openai_error(body: dict, expected_code: str) -> None:
    """Assert the response body uses the OpenAI error envelope (ADR-018)."""
    error = body.get("error", {})
    assert isinstance(error, dict), (
        f"Expected 'error' to be a dict (OpenAI envelope), got {type(error).__name__}: {body}"
    )
    assert error.get("code") == expected_code, (
        f"Expected error.code='{expected_code}', got '{error.get('code')}': {body}"
    )
    assert isinstance(error.get("message"), str) and len(error["message"]) > 0, (
        "error.message must be a non-empty string"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC-001: 10 consecutive calls all return schema-conforming JSON
# ─────────────────────────────────────────────────────────────────────────────


def test_valid_schema_returns_conforming_json(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """
    SC-001: 10 consecutive structured output requests all return schema-conforming JSON.

    Each response content field must be:
    - A string (not an object)
    - Parseable with json.loads()
    - Valid against the provided JSON Schema
    """
    for i in range(10):
        resp = requests.post(
            f"{kong_base_url}/v1/chat/completions",
            json=_so_request(
                model="gpt-4o-mini",
                schema=_INVOICE_SCHEMA,
                name="invoice_schema",
                extra={
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Extract: Invoice INV-{i:03d} total ${(i + 1) * 10}.00",
                        }
                    ]
                },
            ),
            headers=auth_headers,
            timeout=60,
        )
        assert resp.status_code == 200, (
            f"Call {i + 1}/10: Expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        assert isinstance(content, str), (
            f"Call {i + 1}/10: content must be a string, got {type(content).__name__}"
        )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            pytest.fail(f"Call {i + 1}/10: content is not valid JSON: {exc}\ncontent={content!r}")
        try:
            jsonschema.validate(parsed, _INVOICE_SCHEMA)
        except jsonschema.ValidationError as exc:
            pytest.fail(
                f"Call {i + 1}/10: parsed content does not conform to schema: {exc.message}\n"
                f"parsed={parsed!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# SC-002 / FR-001: Invalid schema rejected before inference
# ─────────────────────────────────────────────────────────────────────────────


def test_invalid_json_schema_rejected_before_inference(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """
    SC-002: A schema with an invalid 'type' value is rejected with 422 before any
    model call is made. The response arrives well under any LLM latency budget.
    """
    bad_schema = {"type": "bogus_type_that_does_not_exist"}
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=_so_request(schema=bad_schema, name="bad_schema"),
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 422, (
        f"Expected 422 for invalid schema, got {resp.status_code}: {resp.text}"
    )
    _assert_openai_error(resp.json(), "invalid_json_schema")


def test_model_without_json_schema_capability_rejected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """FR-001: A model not declaring json_schema capability returns 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        # text-embedding-3-small is a chat-incompatible model that won't have json_schema
        json=_so_request(model="text-embedding-3-small"),
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, (
        f"Expected 400 for model without json_schema capability, got {resp.status_code}: {resp.text}"
    )
    _assert_openai_error(resp.json(), "structured_output_model_required")


def test_schema_name_required(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """FR-001: Omitting response_format.name returns 400 invalid_structured_output_request."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {
            "type": "json_schema",
            # no "name" field
            "strict": True,
            "schema": _INVOICE_SCHEMA,
        },
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, (
        f"Expected 400 for missing name, got {resp.status_code}: {resp.text}"
    )
    _assert_openai_error(resp.json(), "invalid_structured_output_request")


def test_schema_field_required(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """FR-001: Omitting response_format.schema returns 400 invalid_structured_output_request."""
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {
            "type": "json_schema",
            "name": "missing_schema",
            "strict": True,
            # no "schema" field
        },
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=payload,
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, (
        f"Expected 400 for missing schema, got {resp.status_code}: {resp.text}"
    )
    _assert_openai_error(resp.json(), "invalid_structured_output_request")


# ─────────────────────────────────────────────────────────────────────────────
# FR-008: Streaming guard
# ─────────────────────────────────────────────────────────────────────────────


def test_streaming_rejected_for_structured_output(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """FR-008: stream:true combined with json_schema mode returns 400."""
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=_so_request(extra={"stream": True}),
        headers=auth_headers,
        timeout=10,
    )
    assert resp.status_code == 400, (
        f"Expected 400 for stream+json_schema, got {resp.status_code}: {resp.text}"
    )
    _assert_openai_error(resp.json(), "structured_output_streaming_not_supported")


# ─────────────────────────────────────────────────────────────────────────────
# FR-008: Non-SO request is unaffected
# ─────────────────────────────────────────────────────────────────────────────


def test_non_so_request_unaffected(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """FR-008: A plain chat completion without response_format is unaffected by the SO gate."""
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
        f"Non-SO request must still return 200. Got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    content = body["choices"][0]["message"].get("content", "")
    assert isinstance(content, str) and len(content) > 0, (
        "Non-SO response must have non-empty content string"
    )


# ─────────────────────────────────────────────────────────────────────────────
# SC-003: Schema conformance failure has distinct error type
# ─────────────────────────────────────────────────────────────────────────────


def test_schema_conformance_failure_has_distinct_error_type(
    kong_base_url: str, auth_headers: dict[str, str]
) -> None:
    """
    SC-003: When all retries are exhausted the platform returns 422 with
    error.type: schema_conformance_failure, retry_count, and schema_name.
    These fields are distinct from all other platform error types.
    """
    # A contradictory schema that no model can satisfy
    impossible_schema = {
        "type": "object",
        "properties": {"x": {"type": "integer", "minimum": 100, "maximum": 5}},
        "required": ["x"],
        "additionalProperties": False,
    }
    resp = requests.post(
        f"{kong_base_url}/v1/chat/completions",
        json=_so_request(
            schema=impossible_schema,
            name="impossible_schema",
            extra={"messages": [{"role": "user", "content": "Provide a value for x."}]},
        ),
        headers=auth_headers,
        timeout=120,  # allow up to MAX_SO_RETRIES attempts
    )
    assert resp.status_code == 422, (
        f"Expected 422 for impossible schema after retries, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    error = body.get("error", {})
    assert error.get("type") == "schema_conformance_failure", (
        f"Expected error.type='schema_conformance_failure', got '{error.get('type')}'"
    )
    assert error.get("code") == "schema_conformance_failure", (
        f"Expected error.code='schema_conformance_failure', got '{error.get('code')}'"
    )
    assert body.get("schema_name") == "impossible_schema", (
        f"Expected schema_name='impossible_schema', got '{body.get('schema_name')}'"
    )
    assert isinstance(body.get("retry_count"), int), (
        f"Expected retry_count to be an int, got {type(body.get('retry_count')).__name__}"
    )
    # Confirm this type is distinct from other known error types
    assert error.get("type") != "all_fallbacks_exhausted"
    assert error.get("type") != "upstream_error"
    assert error.get("type") != "invalid_request_error"


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests for structured_output.py (no live stack needed)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from services.guardrails.structured_output import (  # package import
        StructuredOutputCapabilityCache,
        compute_schema_hash,
        has_structured_output_request,
        inject_schema_metadata,
        validate_structured_output_request,
        validate_structured_output_response,
    )
except ImportError:
    from structured_output import (  # flat import  # type: ignore[no-redef]
        StructuredOutputCapabilityCache,
        compute_schema_hash,
        has_structured_output_request,
        inject_schema_metadata,
        validate_structured_output_request,
        validate_structured_output_response,
    )


def _make_cache(
    native: frozenset[str] = frozenset({"gpt-4o-mini", "claude-sonnet"}),
    prompt: frozenset[str] = frozenset({"gemini-flash"}),
) -> StructuredOutputCapabilityCache:
    cache = StructuredOutputCapabilityCache(
        native_model_names=native,
        prompt_model_names=prompt,
        all_json_schema_models=frozenset(native | prompt),
    )
    return cache


# has_structured_output_request


def test_unit_has_so_request_true() -> None:
    assert has_structured_output_request({"response_format": {"type": "json_schema"}}) is True


def test_unit_has_so_request_false_missing() -> None:
    assert has_structured_output_request({}) is False


def test_unit_has_so_request_false_other_type() -> None:
    assert has_structured_output_request({"response_format": {"type": "json_object"}}) is False


# compute_schema_hash


def test_unit_schema_hash_deterministic() -> None:
    h1 = compute_schema_hash({"type": "object", "properties": {"a": {"type": "string"}}})
    h2 = compute_schema_hash({"properties": {"a": {"type": "string"}}, "type": "object"})
    assert h1 == h2, "Schema hash must be order-independent (sorted keys)"


def test_unit_schema_hash_length() -> None:
    h = compute_schema_hash({"type": "string"})
    assert len(h) == 12, f"Expected 12 hex chars, got {len(h)}"


# inject_schema_metadata


def test_unit_inject_schema_metadata_adds_fields() -> None:
    body = {"model": "gpt-4o-mini", "messages": []}
    result = inject_schema_metadata(body, "my_schema", "abc123456789")
    assert result["metadata"]["schema_name"] == "my_schema"
    assert result["metadata"]["schema_hash"] == "abc123456789"


def test_unit_inject_schema_metadata_does_not_mutate_original() -> None:
    body: dict = {"model": "gpt-4o-mini"}
    inject_schema_metadata(body, "x", "y")
    assert "metadata" not in body


# validate_structured_output_request — rule 1: streaming guard


def test_unit_validate_so_request_streaming_rejected() -> None:
    cache = _make_cache()
    body = {
        "model": "gpt-4o-mini",
        "stream": True,
        "response_format": {"type": "json_schema", "name": "s", "schema": {"type": "object"}},
    }
    result = validate_structured_output_request(body, cache)
    assert result is not None
    error_dict, status = result
    assert status == 400
    assert error_dict["error"]["code"] == "structured_output_streaming_not_supported"


# validate_structured_output_request — rule 2: name required


def test_unit_validate_so_request_name_missing_rejected() -> None:
    cache = _make_cache()
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_schema", "schema": {"type": "object"}},
    }
    result = validate_structured_output_request(body, cache)
    assert result is not None
    _, status = result
    assert status == 400
    assert result[0]["error"]["code"] == "invalid_structured_output_request"


# validate_structured_output_request — rule 3: schema required


def test_unit_validate_so_request_schema_missing_rejected() -> None:
    cache = _make_cache()
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_schema", "name": "s"},
    }
    result = validate_structured_output_request(body, cache)
    assert result is not None
    _, status = result
    assert status == 400
    assert result[0]["error"]["code"] == "invalid_structured_output_request"


# validate_structured_output_request — rule 4: model capability


def test_unit_validate_so_request_unknown_model_rejected() -> None:
    cache = _make_cache()
    body = {
        "model": "unknown-model",
        "response_format": {
            "type": "json_schema",
            "name": "s",
            "schema": {"type": "object"},
        },
    }
    result = validate_structured_output_request(body, cache)
    assert result is not None
    _, status = result
    assert status == 400
    assert result[0]["error"]["code"] == "structured_output_model_required"


# validate_structured_output_request — rule 5: invalid JSON Schema


def test_unit_validate_so_request_invalid_schema_rejected() -> None:
    cache = _make_cache()
    body = {
        "model": "gpt-4o-mini",
        "response_format": {
            "type": "json_schema",
            "name": "s",
            "schema": {"type": "not_a_valid_type"},
        },
    }
    result = validate_structured_output_request(body, cache)
    assert result is not None
    _, status = result
    assert status == 422
    assert result[0]["error"]["code"] == "invalid_json_schema"


# validate_structured_output_request — happy path


def test_unit_validate_so_request_valid_passes() -> None:
    cache = _make_cache()
    body = {
        "model": "gpt-4o-mini",
        "response_format": {
            "type": "json_schema",
            "name": "invoice",
            "strict": True,
            "schema": _INVOICE_SCHEMA,
        },
    }
    result = validate_structured_output_request(body, cache)
    assert result is None, f"Expected None (pass), got {result}"


# validate_structured_output_response — pass


def test_unit_validate_so_response_pass() -> None:
    response = {
        "choices": [
            {"message": {"content": '{"invoice_number": "INV-001", "total": 99.99}'}}
        ]
    }
    assert validate_structured_output_response(response, _INVOICE_SCHEMA) == "pass"


# validate_structured_output_response — retry on non-JSON


def test_unit_validate_so_response_retry_on_non_json() -> None:
    response = {"choices": [{"message": {"content": "not json at all"}}]}
    assert validate_structured_output_response(response, _INVOICE_SCHEMA) == "retry"


# validate_structured_output_response — retry on schema mismatch


def test_unit_validate_so_response_retry_on_schema_mismatch() -> None:
    # missing required field "total"
    response = {"choices": [{"message": {"content": '{"invoice_number": "INV-001"}'}}]}
    assert validate_structured_output_response(response, _INVOICE_SCHEMA) == "retry"


# validate_structured_output_response — retry on missing choices


def test_unit_validate_so_response_retry_on_missing_choices() -> None:
    assert validate_structured_output_response({}, _INVOICE_SCHEMA) == "retry"


# validate_structured_output_response — retry on None content


def test_unit_validate_so_response_retry_on_none_content() -> None:
    response = {"choices": [{"message": {"content": None}}]}
    assert validate_structured_output_response(response, _INVOICE_SCHEMA) == "retry"
