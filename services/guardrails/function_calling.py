"""
Function calling request/response validation and model capability registry.

This module is one of two validation modules for the Guardrails service (the other
being vision.py). It enforces the function calling contract on POST /v1/chat/completions:

  Pre-proxy  : validate_function_calling_request() — rejects bad requests before
               they reach LiteLLM, protecting against wasted provider API calls and
               confusing downstream errors.
  Post-proxy : validate_tool_call_response() — inspects the upstream response and
               guarantees callers always receive well-formed JSON in
               function.arguments (FR-016). This is unique to function calling;
               vision.py has no equivalent response-side check.

The module is consumed exclusively by main.py. All public functions are pure and
stateless except FunctionCallingCapabilityCache, which holds startup state.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm:4000")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY", "")


@dataclass
class FunctionCallingCapabilityCache:
    """
    In-memory registry of model names that declare function-calling capability.

    Why needed: The guardrails service must know which models support function
    calling before it can enforce the capability gate (FR-010). Rather than
    hard-coding model names, this cache loads the list dynamically from LiteLLM's
    /model/info endpoint on startup, keeping the single source of truth in
    services/litellm/config.yaml.

    Relationship to other components:
    - Populated by load(), called once during the FastAPI lifespan in main.py.
    - Stored as app.state.fc_cache so every request handler can access it without
      a per-request HTTP call.
    - Passed into validate_function_calling_request() as fc_models.
    - Mirrors VisionCapabilityCache in vision.py; both follow the same startup
      pattern but track different capability flags ("function-calling" vs "vision").

    Attributes:
        fc_model_names: Immutable set of model name strings where
            "function-calling" appears in model_info.capabilities in config.yaml.
            Empty until load() completes successfully.
    """

    fc_model_names: frozenset[str] = field(default_factory=frozenset)

    async def load(self) -> None:
        """
        Populate fc_model_names from LiteLLM's /model/info endpoint.

        Why needed: LiteLLM is the authoritative registry for model capabilities.
        Fetching from it at startup means capability changes in config.yaml take
        effect on the next container restart without touching guardrails code.

        How it works: Calls GET {LITELLM_BASE_URL}/model/info with the master key,
        iterates the "data" array, and collects model_name values where
        "function-calling" appears in model_info.capabilities.

        Relationship to other functions:
        - Called once by _lifespan() in main.py before the app begins serving.
        - The populated fc_model_names set is consumed by
          validate_function_calling_request() on every function-calling request.

        Inputs: None (reads LITELLM_BASE_URL and LITELLM_MASTER_KEY from env).

        Returns: None. Mutates self.fc_model_names in place.

        Raises:
            RuntimeError: If the /model/info endpoint is unreachable or returns
                a non-2xx response. The FastAPI lifespan treats this as a fatal
                startup failure — the service will not start.
        """
        url = f"{LITELLM_BASE_URL}/model/info"
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            except Exception as exc:
                raise RuntimeError(
                    f"FunctionCallingCapabilityCache: failed to load model info from {url}: {exc}"
                ) from exc

        data = resp.json()
        models = data.get("data", [])
        fc_names: set[str] = set()
        for entry in models:
            info = entry.get("model_info", {})
            capabilities: list[str] = info.get("capabilities", [])
            if "function-calling" in capabilities:
                fc_names.add(entry.get("model_name", ""))
        self.fc_model_names = frozenset(fc_names)


def has_tools(body: dict[str, Any]) -> bool:
    """
    Return True if the request body contains a non-empty tools array.

    Why needed: Acts as the fast-path guard in _validate_function_calling()
    (main.py). If tools is absent or empty and no explicit tool_choice is set,
    the entire function-calling validation pipeline is skipped, keeping the
    overhead for ordinary text-only requests at zero.

    Relationship to other functions:
    - Called by _validate_function_calling() in main.py alongside an explicit
      tool_choice check to decide whether to enter validation.
    - Complements count_tools(), which is used after validation passes to record
      the count in the audit log.

    Args:
        body: Parsed JSON request body as a dict.

    Returns:
        True if body["tools"] is a list with at least one entry; False otherwise
        (absent, null, non-list, or empty list all return False).
    """
    tools = body.get("tools")
    return isinstance(tools, list) and len(tools) > 0


def count_tools(body: dict[str, Any]) -> int:
    """
    Return the number of tool definitions in the request body.

    Why needed: The guardrails audit log records how many tools were present in
    each request (field: tool_count). This gives operators visibility into
    function-calling traffic patterns without logging any sensitive content.

    Relationship to other functions:
    - Called by _validate_function_calling() in main.py after validation passes,
      to supply the tool_count value to _write_audit().
    - has_tools() is its boolean sibling; this function provides the integer count.

    Args:
        body: Parsed JSON request body as a dict.

    Returns:
        Integer length of body["tools"] if it is a list; 0 otherwise.
    """
    tools = body.get("tools")
    return len(tools) if isinstance(tools, list) else 0


def _fc_error(message: str, code: str, status: int) -> tuple[dict[str, Any], int]:
    """
    Build a structured validation-rejection response in the OpenAI error envelope.

    Why needed: All function-calling validation rejections on /v1/chat/completions
    must use the OpenAI error envelope format (ADR-018) so that OpenAI SDK clients
    can parse them natively. This helper centralises envelope construction and
    avoids repeating the dict shape at every call site.

    Relationship to other functions:
    - Called exclusively by validate_function_calling_request() to produce the
      error payload for each of its five validation rules.
    - Uses the same envelope shape as _openai_error() in vision.py; the two are
      independent copies to keep the modules decoupled.

    Args:
        message: Human-readable explanation surfaced to the API caller.
        code:    Machine-readable error code (e.g. "function_calling_model_required").
                 Matches the codes defined in specs/019-function-calling/data-model.md.
        status:  HTTP status code to return (typically 400).

    Returns:
        A 2-tuple of (error_dict, http_status). The dict has the shape:
        {"error": {"message": ..., "type": "invalid_request_error", "code": ...}}.
    """
    return (
        {"error": {"message": message, "type": "invalid_request_error", "code": code}},
        status,
    )


def validate_function_calling_request(
    body: dict[str, Any],
    fc_models: frozenset[str],
) -> tuple[dict[str, Any], int] | None:
    """
    Validate a chat completion request body that contains tools or an explicit tool_choice.

    Why needed: LiteLLM passes tools[] to providers without any gateway-level
    validation. Without this function, callers using a non-function-capable model
    (e.g. claude-haiku) would receive a confusing provider-level error instead of a
    clear 400. This function enforces five rules that protect both the caller
    (actionable errors) and the platform (no wasted provider API calls).

    Validation rules (applied in order; returns on first failure):
      1. stream + tools → 400 function_calling_streaming_not_supported
         Streaming function calling is out of scope for v1; reject early.
      2. Model not in fc_models → 400 function_calling_model_required
         Only models declaring "function-calling" in capabilities may receive tools.
      3. tool_choice is an explicit value (not "auto"/"none") but tools is empty
         → 400 tool_choice_requires_tools
         A forced tool_choice is meaningless without a tools array.
      4. tool_choice names a function not present in tools
         → 400 tool_choice_function_not_found
         Prevents a trivially wrong request reaching the provider.
      5. Any tool definition is missing "name" or has a non-dict "parameters"
         → 400 invalid_tool_definition
         LiteLLM forwards tool definitions verbatim; structurally invalid definitions
         cause opaque provider errors without this check.

    Important: tool_choice="none" is intentionally NOT rejected. It is a valid
    OpenAI API value that suppresses tool invocation, so it must reach LiteLLM
    unchanged.

    Relationship to other functions:
    - Called by _validate_function_calling() in main.py, which is the pre-proxy
      gate in the proxy() request handler.
    - Receives fc_models from FunctionCallingCapabilityCache (loaded at startup).
    - Uses _fc_error() to build every error response.
    - validate_tool_call_response() is its post-proxy counterpart; this function
      validates the request, that function validates the response.

    Args:
        body:      Parsed JSON request body. Must contain at minimum "model" and
                   "tools" keys; "tool_choice" is optional.
        fc_models: Frozen set of model name strings that declare "function-calling"
                   capability, as loaded by FunctionCallingCapabilityCache.load().

    Returns:
        None if all checks pass (request is safe to forward to LiteLLM).
        A 2-tuple of (error_dict, http_status) on the first rule violation.
        The error_dict follows the OpenAI error envelope:
        {"error": {"message": ..., "type": "invalid_request_error", "code": ...}}.
    """
    tools: list[dict[str, Any]] = body.get("tools") or []

    # 1. Streaming + tools is not supported in v1.
    if body.get("stream") is True:
        return _fc_error(
            "Streaming is not supported for function calling requests. "
            "Set 'stream' to false or omit it.",
            "function_calling_streaming_not_supported",
            400,
        )

    # 2. Requested model must declare function-calling capability.
    model: str = body.get("model", "")
    if not model:
        return _fc_error(
            "A model must be specified for function calling requests. "
            f"Use a function-capable model: {', '.join(sorted(fc_models))}.",
            "function_calling_model_required",
            400,
        )
    if model not in fc_models:
        return _fc_error(
            f"Model '{model}' does not support function calling. "
            f"Use a function-capable model: {', '.join(sorted(fc_models))}.",
            "function_calling_model_required",
            400,
        )

    # 3. tool_choice consistency: if set (and not "auto"/"none"), tools must be non-empty.
    tool_choice = body.get("tool_choice")
    if tool_choice is not None and tool_choice not in ("auto", "none") and not tools:
        return _fc_error(
            "tool_choice is set but the tools array is empty or absent.",
            "tool_choice_requires_tools",
            400,
        )

    # 4. tool_choice object: named function must exist in tools.
    if isinstance(tool_choice, dict):
        tc_fn = tool_choice.get("function", {}).get("name", "")
        tool_names = {
            t.get("function", {}).get("name", "")
            for t in tools
            if isinstance(t, dict)
        }
        if tc_fn and tc_fn not in tool_names:
            return _fc_error(
                f"tool_choice references function '{tc_fn}' which is not defined "
                "in the tools array.",
                "tool_choice_function_not_found",
                400,
            )

    # 5. Tool definition structure: each entry needs name + parameters as dict.
    for idx, tool in enumerate(tools):
        if not isinstance(tool, dict):
            return _fc_error(
                f"Tool at index {idx} is not a valid object.",
                "invalid_tool_definition",
                400,
            )
        fn = tool.get("function", {})
        if not isinstance(fn, dict) or not fn.get("name"):
            return _fc_error(
                f"Tool at index {idx} is missing required field 'name'.",
                "invalid_tool_definition",
                400,
            )
        params = fn.get("parameters")
        if params is not None and not isinstance(params, dict):
            return _fc_error(
                f"Tool at index {idx} has a 'parameters' value that is not a JSON object.",
                "invalid_tool_definition",
                400,
            )

    return None


def validate_tool_call_response(
    response_body: dict[str, Any],
) -> tuple[dict[str, Any], int] | None:
    """
    Inspect an upstream LiteLLM response to guarantee all tool call arguments are valid JSON.

    Why needed: The OpenAI API spec and FR-004 require that function.arguments is
    always a valid JSON-parseable string. In practice, some provider responses can
    return malformed JSON (e.g. a bare string or truncated object). If such a
    response were forwarded to the caller, it would silently break any client that
    calls json.loads(arguments) without a try/except. This function catches that
    failure at the gateway and returns an explicit 502 instead.

    This is the post-proxy counterpart to validate_function_calling_request():
    validate_function_calling_request() guards the inbound request;
    this function guards the outbound response.

    Relationship to other functions:
    - Called by proxy() in main.py immediately after receiving an HTTP 200 response
      from LiteLLM when the original request contained tools (_had_tools == True).
    - Only called for HTTP 200 responses; non-200 upstream errors (e.g. 503) are
      handled by _normalise_503() and bypass this check.
    - Has no dependency on FunctionCallingCapabilityCache; it works purely on the
      response body content.

    Args:
        response_body: The parsed JSON body of the upstream LiteLLM response.
                       Expected to be an OpenAI chat completion response shape.

    Returns:
        None if all tool_calls[*].function.arguments values parse successfully as
        JSON, or if no tool_calls are present in the response.
        A 2-tuple of (error_dict, 502) if any arguments value fails json.loads().
        The error_dict uses "type": "upstream_error" to signal that the fault
        originates from the provider, not from the caller's request.
    """
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return None

    tool_calls = choices[0].get("message", {}).get("tool_calls")
    if not isinstance(tool_calls, list):
        return None

    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        arguments = call.get("function", {}).get("arguments")
        if arguments is None:
            continue
        try:
            json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return (
                {
                    "error": {
                        "message": "Upstream model returned malformed tool call arguments "
                        "that could not be parsed as JSON.",
                        "type": "upstream_error",
                        "code": "invalid_tool_arguments",
                    }
                },
                502,
            )

    return None
