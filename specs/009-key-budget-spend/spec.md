# Feature Specification: API Key Budget Enforcement & Spend Tracking

**Feature Branch**: `009-key-budget-spend`

**Created**: 2026-05-28

**Status**: Draft

**Input**: Build token-level cost tracking that attributes the dollar cost of every request to the API key that made it and enforces hard monthly budget limits. When a key reaches its budget the platform must reject further requests. A spend report endpoint must return breakdown by key and model. Budget periods reset monthly. Spend must be linkable to the prompt version that generated it in Langfuse.

## Clarifications

### Session 2026-05-28

- Q: How is spend calculated per request? → A: spend = (prompt_tokens × input_rate + completion_tokens × output_rate) / 1,000,000
- Q: When does the monthly budget period reset? → A: First day of each calendar month (UTC)
- Q: Who can access the spend report endpoint? → A: GET /v1/spend requires master key authentication
- Q: What HTTP status and error code are returned when a key exhausts its budget? → A: HTTP 429 with error code `budget_exceeded`
- Q: How is spend linked to Langfuse prompt versions? → A: Cost metadata is attached to each Langfuse trace so per-prompt-version cost is queryable from the Langfuse dashboard
- Q: What happens when the spend store is unavailable during a budget check? → A: Fail closed — return HTTP 503 with error code `spend_store_unavailable`; no request is forwarded to the upstream model

## User Scenarios & Testing

### User Story 1 - Per-Request Cost Attribution (Priority: P1)

A platform operator issues API requests via virtual keys. Every request's dollar cost is computed from token counts and per-model rates, then accumulated against the key that made the request for the current calendar month.

**Why this priority**: Without accurate per-key attribution, budget enforcement and spend reporting have no foundation.

**Independent Test**: Issue a chat completion request through a virtual key, then query the spend report and confirm the key's accumulated cost increased by the expected amount for that request.

**Acceptance Scenarios**:

1. **Given** a request completes with `prompt_tokens=100` and `completion_tokens=50` for a model with `input_rate=2.50` and `output_rate=10.00`, **When** spend is computed, **Then** the recorded cost equals `(100×2.50 + 50×10.00) / 1,000,000 = $0.000750`.
2. **Given** a virtual key has made multiple requests in the current month, **When** the spend report is queried, **Then** the key's total equals the sum of all individual request costs for that calendar month.
3. **Given** a new calendar month begins, **When** the first request of the month is made, **Then** accumulated spend for that key resets to zero before applying the new request's cost.

---

### User Story 2 - Hard Budget Enforcement (Priority: P1)

A platform operator sets a monthly dollar budget on a virtual key. Once cumulative spend for that key reaches or exceeds the budget ceiling, all subsequent requests are rejected for the remainder of the month.

**Why this priority**: Budget enforcement is the primary safety guarantee — without it the spend tracking feature delivers no operational protection.

**Independent Test**: Set a key's budget to $0.01, exhaust it with requests, then issue one more request and confirm it is rejected with HTTP 429 and error code `budget_exceeded`.

**Acceptance Scenarios**:

1. **Given** a virtual key with a $1.00 monthly budget has accumulated $0.999, **When** a request that would cost $0.002 is received, **Then** the request is rejected before forwarding to the upstream model with HTTP 429 `{"error": {"code": "budget_exceeded", "message": "..."}}`.
2. **Given** a key has been blocked due to budget exhaustion, **When** the first day of the next calendar month arrives, **Then** the key's spend resets to zero and subsequent requests are accepted again (assuming budget has not been reduced further).
3. **Given** a key has no budget configured, **When** any request is made, **Then** spend is recorded but no rejection occurs.

---

### User Story 3 - Spend Report by Key and Model (Priority: P2)

A platform operator queries `GET /v1/spend` using a master key and receives a breakdown of spend for the current month organised by virtual key and by model.

**Why this priority**: Visibility into spend distribution is needed for chargeback, capacity planning, and cost governance.

**Independent Test**: Issue requests on two different virtual keys using two different models, then call `GET /v1/spend` with a master key and confirm the response includes per-key and per-model rows with correct cost figures.

**Acceptance Scenarios**:

1. **Given** requests from three virtual keys across two models in the current month, **When** `GET /v1/spend` is called with a valid master key, **Then** the response includes one row per (key, model) combination with `key_id`, `model`, `prompt_tokens`, `completion_tokens`, and `cost_usd` fields.
2. **Given** `GET /v1/spend` is called without a master key or with an invalid key, **Then** the request is rejected with HTTP 401.
3. **Given** `GET /v1/spend` is called with an optional `key_id` query parameter, **Then** the response is filtered to rows for that key only.

---

### User Story 4 - Langfuse Prompt-Version Cost Linkage (Priority: P2)

Every request that was initiated via a versioned Langfuse prompt has its computed cost attached as metadata on the Langfuse trace, enabling cost queries grouped by prompt name and version in the Langfuse dashboard.

**Why this priority**: Prompt governance requires knowing which prompt versions are expensive so operators can optimise before promoting to production.

**Independent Test**: Issue a request referencing a Langfuse prompt name and version, then inspect the resulting Langfuse trace and confirm it carries `cost_usd`, `prompt_tokens`, and `completion_tokens` metadata attributes.

**Acceptance Scenarios**:

1. **Given** a request carries `langfuse_prompt_name` and `langfuse_prompt_version` metadata, **When** the request completes, **Then** the Langfuse trace for that request includes `cost_usd`, `prompt_tokens`, `completion_tokens`, and `model` as trace-level metadata.
2. **Given** multiple requests reference the same prompt version, **When** the Langfuse dashboard is queried, **Then** per-prompt-version total cost can be derived by summing `cost_usd` across matching traces.
3. **Given** a request does not carry prompt metadata, **When** the request completes, **Then** cost metadata is still attached to the Langfuse trace but without prompt attribution fields.

---

### Edge Cases

- What happens when token counts are missing from the upstream model response? (Cost recorded as $0.00; flagged in structured logs.)
- What happens when a model has no configured rate? (Request proceeds; cost recorded as $0.00 with a warning log entry; spend report shows $0.)
- What happens if two requests for the same key arrive simultaneously near the budget ceiling? (Both are checked against the persisted accumulated total before forwarding; the one that causes the ceiling to be crossed is rejected.)
- What happens when the spend store is unavailable during a budget check? (Request is rejected with HTTP 503 `spend_store_unavailable`; nothing is forwarded to the upstream model — the budget ceiling is treated as a hard guarantee even under partial failure.)
- What happens when the budget value is updated mid-month? (New limit takes effect immediately; if current spend already exceeds the new limit, subsequent requests are rejected until reset.)

## Requirements

### Functional Requirements

- **FR-001**: System MUST compute the dollar cost of every completed inference request using the formula: `cost = (prompt_tokens × input_rate + completion_tokens × output_rate) / 1,000,000`.
- **FR-002**: System MUST persist per-model input and output rates (USD per million tokens) and make them available at request-completion time.
- **FR-003**: System MUST accumulate computed cost against the virtual key that authenticated the request for the current calendar-month period.
- **FR-004**: System MUST reset accumulated spend for all keys to zero on the first day (00:00:00 UTC) of each calendar month.
- **FR-005**: System MUST reject any inbound request from a virtual key whose accumulated monthly spend meets or exceeds its configured budget ceiling, returning HTTP 429 with error code `budget_exceeded`.
- **FR-006**: System MUST allow virtual keys to exist without a budget ceiling, in which case spend is tracked but no rejection is applied.
- **FR-007**: System MUST expose `GET /v1/spend` returning current-month spend aggregated by virtual key and by model, accessible only with a master key.
- **FR-008**: `GET /v1/spend` MUST support an optional `key_id` query parameter to filter results to a single key.
- **FR-009**: System MUST attach `cost_usd`, `prompt_tokens`, `completion_tokens`, and `model` as metadata on every Langfuse trace it emits.
- **FR-010**: When a request carries Langfuse prompt metadata (`prompt_name`, `prompt_version`), those values MUST also be included on the trace so cost is attributable by prompt version in Langfuse.
- **FR-011**: Budget ceiling updates on a key MUST take effect immediately for all subsequent requests within the current month.
- **FR-012**: If the spend store is unreachable when a budget check is attempted, the system MUST reject the request with HTTP 503 and error code `spend_store_unavailable` — no request MUST be forwarded to the upstream model while the check cannot be evaluated.

### Key Entities

- **VirtualKey**: A scoped API credential issued to a consumer. Carries an optional `monthly_budget_usd` ceiling and accumulates `current_period_spend_usd` for the active calendar month.
- **ModelRate**: Records the `input_rate_per_million` and `output_rate_per_million` USD pricing for each model identifier. Updated when provider pricing changes.
- **SpendRecord**: An immutable log entry per completed request: `key_id`, `model`, `prompt_tokens`, `completion_tokens`, `cost_usd`, `langfuse_trace_id`, `prompt_name`, `prompt_version`, `timestamp`.
- **BudgetPeriod**: Tracks the current active month (`year`, `month`) so the system knows when to trigger a reset and verify a record belongs to the current period.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Cost for every completed request is recorded within the same synchronous response cycle — operators see spend totals update in real time without batch delay.
- **SC-002**: A key that exhausts its monthly budget receives HTTP 429 on the very next request — zero requests are forwarded to upstream models after the ceiling is crossed.
- **SC-003**: `GET /v1/spend` returns a complete breakdown for the current month in under 500 ms for up to 10,000 active keys.
- **SC-004**: Monthly budget resets complete by 00:05:00 UTC on the first day of each month — no key carries prior-month spend into the new period.
- **SC-005**: 100% of completed inference requests have a corresponding Langfuse trace carrying `cost_usd`, `prompt_tokens`, and `completion_tokens` metadata.
- **SC-006**: For requests with prompt attribution, Langfuse dashboard can aggregate total cost per prompt version without any additional data export.

## Assumptions

- Model rate tables are managed by the platform operator and pre-populated before the feature is enabled; the feature does not auto-discover pricing from provider APIs.
- The platform's existing virtual-key mechanism (managed by LiteLLM) is the authoritative source of key identity — this feature extends it rather than replacing it.
- "Master key" refers to the existing LiteLLM master key credential already in use on the platform.
- Monthly period boundaries are evaluated in UTC.
- Spend accumulation and budget enforcement execute synchronously in the request path before the request is forwarded to the upstream model, accepting a small latency addition in exchange for correctness.
- Concurrent request safety for spend accumulation relies on atomic increment operations; distributed locking is out of scope for v1.
- Historical spend data beyond the current calendar month is retained for audit purposes but is outside the scope of the `GET /v1/spend` endpoint for this feature.
- The feature integrates with LiteLLM's existing callback mechanism to capture token counts from model responses.
