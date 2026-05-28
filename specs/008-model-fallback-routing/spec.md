# Feature Specification: Automatic Model Fallback Routing

**Feature Branch**: `008-model-fallback-routing`

**Created**: 2026-05-28

**Status**: Draft

**Input**: User description: "Build automatic fallback routing so that when a primary model or provider fails the platform silently retries the request on a backup model without the caller experiencing an error. Fallback chains must be configurable per model. When all fallbacks are exhausted a 503 must be returned. The model actually used must be visible in the response. When a request exceeds the primary model's context window the platform must route to a larger-context alternative automatically."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Silent Fallback on Provider Failure (Priority: P1)

A caller requests a model whose provider is temporarily unavailable or returns an error. The platform automatically retries the request on the next model in the configured fallback chain and returns a successful response. The caller receives no error and does not need to retry.

**Why this priority**: This is the core reliability guarantee. Without it, any provider outage directly causes caller-visible failures. It is the foundational behaviour all other fallback stories build on.

**Independent Test**: Trigger a provider-level error on the primary model (e.g., by using an invalid API key for that provider only). Send a request for the primary model. Verify the response is HTTP 200 with a valid completion. Verify the response indicates the fallback model was used.

**Acceptance Scenarios**:

1. **Given** the primary model's provider is unavailable, **When** a caller sends a request for that model, **Then** the platform retries on the first fallback model and returns HTTP 200 with a valid completion — no error is surfaced to the caller.
2. **Given** both the primary and first fallback are unavailable, **When** a request is submitted, **Then** the platform attempts each remaining fallback in chain order until one succeeds.
3. **Given** a successful fallback response, **When** the caller inspects the response, **Then** the response identifies the model that actually fulfilled the request, not the originally requested model.
4. **Given** a fallback succeeds, **When** the caller makes a subsequent request for the original model, **Then** the platform retries the primary model first (fallback state is per-request, not persistent).

---

### User Story 2 — 503 When All Fallbacks Exhausted (Priority: P1)

When the primary model and every configured fallback are all unavailable or fail, the platform returns HTTP 503 with a structured error body. The caller can distinguish this from a request-level error (400) or an auth error (401).

**Why this priority**: Without a defined exhaustion behaviour, callers cannot reliably handle the worst-case scenario. This is a correctness requirement paired with US1.

**Independent Test**: Configure a model with a fallback chain where all models are intentionally unavailable. Send a request. Verify HTTP 503 is returned with a structured error body that clearly indicates all fallbacks were exhausted.

**Acceptance Scenarios**:

1. **Given** all models in the fallback chain (primary + all backups) are unavailable, **When** a request is submitted, **Then** HTTP 503 is returned with an error body that indicates all fallbacks were exhausted.
2. **Given** the 503 response, **When** the caller inspects the error body, **Then** the body follows the platform's standard structured error format (`error`, `message`, `detail`).
3. **Given** a 503 is returned, **When** the caller retries the same request after the provider recovers, **Then** the platform serves the request normally from the primary or first available fallback.

---

### User Story 3 — Context Window Overflow Routed to Larger Alternative (Priority: P1)

A caller submits a request whose messages exceed the primary model's context window. Rather than returning a 400 error, the platform automatically routes the request to a configured larger-context alternative model and returns a successful response.

**Why this priority**: Context window limits are a frequent silent failure mode. Automatic rerouting on overflow prevents callers from needing to implement context-management workarounds per model. This is as critical as failure fallback for long-document use cases.

**Independent Test**: Submit a request with a messages payload that exceeds the primary model's documented context window. Verify the response is HTTP 200 with a valid completion from the larger-context alternative. Verify the response identifies the alternative model used.

**Acceptance Scenarios**:

1. **Given** a request whose total token count exceeds the primary model's context window, **When** the request is submitted, **Then** the platform routes to the configured larger-context alternative and returns HTTP 200.
2. **Given** a context-overflow routing event, **When** the caller inspects the response, **Then** the response identifies the larger-context model that was used.
3. **Given** the request fits within the primary model's context window, **When** submitted, **Then** the primary model is used and no overflow routing occurs.
4. **Given** a request overflows and the configured overflow alternative is also unavailable, **When** submitted, **Then** the platform attempts the overflow alternative's own fallback chain; if all are exhausted, HTTP 503 is returned.

---

### User Story 4 — Fallback Chains Are Operator-Configurable Per Model (Priority: P2)

The platform operator can define, update, and remove fallback chains for any model without redeploying application code. Changes take effect on subsequent requests.

**Why this priority**: Without operator control over chains, the fallback behaviour is static and cannot adapt to new providers, deprecations, or cost optimisations. Dependent on US1 being live.

**Independent Test**: Add a new fallback entry to a model's chain via configuration, restart the routing component, and verify the new fallback is attempted when the primary fails. Remove a fallback entry and verify it is no longer attempted.

**Acceptance Scenarios**:

1. **Given** an operator adds a new model to a fallback chain, **When** the primary fails on the next request, **Then** the new model is included in the retry sequence in the specified order.
2. **Given** an operator removes a model from a fallback chain, **When** the primary fails, **Then** the removed model is not attempted.
3. **Given** an operator changes the order of fallback models, **When** the primary fails, **Then** the updated order is used for retries.
4. **Given** a model has no fallback chain configured, **When** it fails, **Then** HTTP 503 is returned immediately (no fallback attempted).

---

### Edge Cases

- What happens when the fallback model returns a different response schema than the primary? → The platform normalises all responses to the same OpenAI-compatible schema regardless of which model fulfilled the request.
- What happens if a fallback model also hits a context window limit? → The platform treats context window overflow as a failure and continues to the next fallback in chain order.
- What happens when a request times out on the primary (vs. returning an explicit error)? → Provider timeouts are treated as failures and trigger the next fallback.
- What happens if the same model appears twice in a fallback chain? → The duplicate is attempted only once; repeat entries are skipped.
- What happens when a fallback model is in cooldown? → It is skipped in chain order; the next non-cooled-down model is attempted. If all remaining models are in cooldown or exhausted, HTTP 503 is returned.
- What happens when all models in the chain are in cooldown simultaneously? → HTTP 503 is returned immediately with a structured error body indicating all models are either exhausted or in cooldown.
- What happens to observability when a fallback occurs? → Each attempt (primary and each fallback) is recorded. The final response trace identifies the model that fulfilled the request.
- What happens to billing and spend attribution when a fallback is used? → Spend is attributed to the model that actually fulfilled the request, not the originally requested model.
- What if the caller explicitly requests a specific model version not in any fallback chain? → The request is attempted; if it fails and no chain is configured for that exact model alias, HTTP 503 is returned immediately.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a primary model fails due to a provider error or timeout, the platform MUST automatically retry the request on the next model in the configured fallback chain without surfacing an error to the caller.
- **FR-002**: Fallback retries MUST proceed through the chain in configured order, attempting each model exactly once per request.
- **FR-003**: When all models in the fallback chain (primary + all backups) fail, the platform MUST return HTTP 503 with a structured error body in the format `{"error": "...", "message": "...", "detail": {...}}`.
- **FR-004**: Every response MUST identify the model that actually fulfilled the request. When a fallback is used, the model field in the response MUST reflect the fallback model, not the originally requested model.
- **FR-005**: When a request's total token count exceeds the primary model's context window limit, the platform MUST automatically route the request to the configured larger-context alternative for that model. Context window overflow is detected by comparing the request token count against the primary model's documented token limit before dispatching to the provider.
- **FR-006**: Context window overflow routing MUST be treated as a fallback event: the overflow alternative's own fallback chain applies if the alternative is also unavailable.
- **FR-007**: Fallback chains MUST be configurable per model alias by the platform operator without redeploying application code.
- **FR-008**: Each fallback attempt MUST be recorded in platform observability, including which model was attempted, the failure reason (provider error, timeout, context overflow), and the final outcome. Fallback events MUST be recorded as Phoenix span attributes on the LLM trace span: `llm.fallback.triggered` (bool), `llm.fallback.reason` (provider_error | timeout | context_overflow), `llm.model.requested` (original model alias), `llm.fallback.attempt_count` (int).
- **FR-009**: Spend and cost attribution MUST be recorded against the model that fulfilled the request, not the originally requested model.
- **FR-010**: A model with no configured fallback chain MUST return HTTP 503 immediately when it fails, with no retry.
- **FR-011**: Fallback routing MUST NOT expose provider-internal error details to the caller in the success response. The caller receives a valid completion as if the primary model had responded.
- **FR-012**: Duplicate model entries in a fallback chain MUST be attempted only once; subsequent duplicate entries are skipped.
- **FR-013**: Any model (primary or fallback) that accumulates 3 consecutive failures MUST enter a 60-second cooldown and be excluded from routing for that window.
- **FR-015**: Each model in the fallback chain MUST be retried up to 2 times before the chain advances to the next model. Retry count resets per model; there is no shared request-level retry budget.
- **FR-014**: Cooldown state is tracked per model alias. A model in cooldown is skipped in the fallback chain as if it were unavailable; the next non-cooled-down model in the chain is attempted instead.

### Key Entities

- **Fallback Chain**: An ordered list of model aliases to attempt when the primary model fails. Associated with exactly one primary model alias. May be empty (no fallback).
- **Fallback Attempt**: A single routing event — one model tried, one outcome (success, provider error, timeout, context overflow). Part of the request's routing history.
- **Routing Decision**: The outcome of processing a request through the fallback chain. Attributes: original model requested, model that fulfilled the request, number of attempts, failure reasons for each failed attempt.
- **Context Overflow Event**: A specific fallback trigger where the request token count exceeds the primary model's documented context window. Treated identically to a provider failure for routing purposes.
- **Cooldown State**: A per-model-alias record tracking consecutive failure count and cooldown expiry timestamp. A model with 3 consecutive failures is excluded from routing until the 60-second cooldown expires. Cooldown state is in-process and resets on service restart.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When a primary model fails, 100% of requests with a configured fallback chain receive a successful response (HTTP 200) without caller retry, provided at least one fallback model is available.
- **SC-002**: When all fallbacks are exhausted, HTTP 503 is returned with a structured error body in 100% of cases — no unhandled exceptions or non-503 error codes are returned.
- **SC-003**: The model field in every successful response accurately identifies the model that fulfilled the request; verified by comparing the response model field against observability logs for 100% of fallback-routed requests.
- **SC-004**: Context window overflow requests are successfully routed to a larger-context alternative in 100% of cases where such an alternative is configured, without caller-visible error.
- **SC-005**: Every fallback attempt (success or failure) is queryable from the observability stack; operators can reconstruct the full routing history for any request using the request ID.
- **SC-006**: Fallback chain configuration changes take effect within one service restart cycle — no code redeployment required.
- **SC-007**: Spend attribution in observability records the fulfilling model, not the requested model, for 100% of fallback-routed requests.

## Clarifications

### Session 2026-05-28

- Q: Does the 60-second cooldown apply to any model in the fallback chain or only the primary? → A: Any model (primary or fallback) that accumulates 3 consecutive failures enters cooldown for 60 seconds and is excluded from routing during that window.
- Q: What Phoenix span attribute names are used for fallback events? → A: `llm.fallback.triggered` (bool), `llm.fallback.reason` (provider_error | timeout | context_overflow), `llm.model.requested` (original model alias), `llm.fallback.attempt_count` (int) — following the OpenInference prefix convention.
- Q: Do the 2 retries apply per model in the chain or as a total request-level budget? → A: Per model — each model in the chain gets up to 2 retry attempts before the chain advances to the next model.

## Assumptions

- Fallback chains are defined in the platform's central model routing configuration; callers cannot specify fallback preferences per request.
- A "failure" that triggers fallback includes: HTTP 4xx/5xx from the provider, connection timeout, and context window exceeded. Rate-limit errors (429) are also treated as failures that trigger fallback.
- The platform already maintains a model catalogue with documented context window sizes per model alias; this feature reads those sizes for overflow detection.
- Fallback routing adds latency equal to the sum of failed attempt durations plus the successful attempt duration; no latency SLA is defined for the worst-case all-fail path (which terminates in 503).
- The caller's request payload is forwarded unchanged to each fallback model; no prompt modification or truncation is performed during fallback.
- Existing fallback chains for four model aliases are already partially defined in the platform configuration from prior phases; this feature formalises and extends that mechanism to be fully configurable and observable.
- Context window overflow alternative models are configured separately from error fallback chains; a model may have both an error fallback chain and a designated overflow alternative.
- The platform does not cache partial responses from failed primary attempts; each fallback attempt starts a fresh provider call with the full original request.
