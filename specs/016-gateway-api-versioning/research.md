# Research: Gateway API Versioning

**Branch**: `016-gateway-api-versioning` | **Date**: 2026-06-03

## Findings

---

### Decision 1: Header value format — `1`/`2` not `v1`/`v2`

**Decision**: Use integer values `1` and `2` for `X-API-Version`.

**Rationale**: The existing global `response-transformer` plugin already emits `X-API-Version: 1` (integer, no prefix). The constitution §4.3 canonically specifies `X-API-Version: 1`. The spec draft used `v1`/`v2` — this is corrected here. Keeping integer values avoids a breaking change to any integration already reading the header.

**Alternatives considered**: `v1`/`v2` string values. Rejected — the existing plugin and constitution already fix the format as integer.

---

### Decision 2: Where to scope the `X-API-Version` header plugin

**Decision**: Remove `X-API-Version` from the global `response-transformer` and add it as a service-scoped `response-transformer` on each service definition (`litellm`, `litellm-v2`, `litellm-embeddings`, `litellm-admin`).

**Rationale**: In Kong's `header_filter` phase, route-scoped plugins execute before service-scoped plugins, which execute before global plugins. All instances of `response-transformer` run as separate plugin instances. The `add` action in response-transformer only adds the header if absent — it does NOT overwrite. This means:
- If global plugin uses `add X-API-Version: 1` and a v2 service uses `replace X-API-Version: 2`, the sequence is: service plugin runs first (replace is a no-op since header absent), then global plugin runs and adds `1`. Result: every route gets `1`, including v2 — WRONG.
- Moving `X-API-Version` to service scope and removing it from global scope is the only clean declarative solution. Each service sets its own correct value; the global plugin retains only `X-Platform: inference-platform`.

**Alternatives considered**:
- Route-level `post-function` Lua plugin to force-set the header. Rejected — adds Lua code maintenance burden; declarative config is sufficient.
- Global plugin with per-route `replace`. Rejected — execution order means the replace runs before the global `add`, making it a no-op.
- Single global plugin with `X-API-Version: 1` for all routes including v2. Rejected — v2 routes would incorrectly report version 1.

---

### Decision 3: v2 routing target

**Decision**: Both v1 and v2 services upstream to `http://litellm:4000`. No separate v2 backend.

**Rationale**: Confirmed by user clarification. The version prefix differentiates the routing tier and response header, not the backend target. LiteLLM handles both sets of requests identically at this stage. Future breaking changes in v2 would be implemented in LiteLLM config or as a separate upstream, at that time.

**Alternatives considered**: Separate upstream for v2 (e.g., a v2-specific LiteLLM instance). Rejected — not needed at this stage; adds operational complexity with no benefit.

---

### Decision 4: Deprecation header set

**Decision**: Deprecated endpoints emit three headers: `Deprecation` (RFC 8594 announcement date as HTTP-date), `Sunset` (RFC 8594 removal date as HTTP-date), `Link` (`<url>; rel="deprecation"`).

**Rationale**: RFC 8594 defines `Deprecation` and `Sunset` as the standard pair for signalling API lifecycle. `Link` with `rel="deprecation"` provides clients a machine-readable pointer to the migration documentation. All three are added via `response-transformer` at the route level (using `add` action — they are not present on non-deprecated routes, so no conflict with global plugin).

**Alternatives considered**: Custom `X-Deprecation` header with a JSON body. Rejected — non-standard; breaks client tooling that understands RFC 8594.

---

### Decision 5: Unknown version prefix handling

**Decision**: Requests to undefined version prefixes (e.g., `/v3/`, `/v0/`) return Kong's native 404. No `X-API-Version` header is injected.

**Rationale**: Kong returns a 404 for any path with no matching route. Since no route is defined for `/v3/` etc., this behaviour is automatic — no configuration needed. The global plugin does not inject `X-API-Version` (it is removed from global scope in Decision 2), so undefined-version responses correctly carry no version header.

**Alternatives considered**: Explicit route for unknown versions returning a custom 404 body with a "version not supported" message. Deferred — acceptable improvement but not required for this feature's acceptance criteria.

---

### Decision 6: 6-month minimum enforcement for sunset dates

**Decision**: The 6-month minimum is enforced by operator process and documented convention, not by a Kong plugin validation.

**Rationale**: Kong's `response-transformer` is stateless — it injects whatever date string it is configured with. Runtime enforcement would require a custom Lua plugin or admin-API validation hook. At this scale, a documented convention (operators are responsible for configuring dates ≥ 6 months out, enforced by PR review) is sufficient. A validation script can be added to the pre-commit or CI pipeline as a future hardening task.

**Alternatives considered**: Custom Lua validation plugin. Deferred to a future hardening task.

---

### Decision 7: Pre-existing guardrails routing gap

**Decision**: This feature does not change the upstream target for any route. All routes (v1 and v2) upstream to `http://litellm:4000` — matching the existing pattern.

**Rationale**: The existing Kong config already routes directly to LiteLLM, bypassing the Guardrails service. The Kong config comment acknowledges this: "guardrails added in Phase 03". This gap predates this feature. Fixing it (routing Kong → Guardrails → LiteLLM) is out of scope here and must be addressed in a dedicated guardrails-wiring feature. Introducing a guardrails dependency in this feature would conflate two separate concerns and risk destabilising the existing auth/rate-limit work.

**Alternatives considered**: Fix the guardrails routing as part of this feature. Rejected — out of scope; should be a separate feature with its own spec and acceptance tests.

---

### Decision 8: Model name alias version-stability

**Decision**: No configuration changes needed in LiteLLM or Kong to achieve model name alias stability.

**Rationale**: Since both v1 and v2 route to the same LiteLLM upstream (`http://litellm:4000`), and LiteLLM strips the `/v1/` or `/v2/` prefix (via `strip_path: false` — the prefix is preserved but LiteLLM uses the remainder for routing), model resolution is identical for both versions. The model catalogue in `services/litellm/config.yaml` is shared. No alias remapping is required.

**Note**: `strip_path: false` means Kong forwards `/v2/chat/completions` to LiteLLM as `/v2/chat/completions`. LiteLLM must handle `/v2/` paths. Since LiteLLM's OpenAI-compatible endpoint is `/v1/chat/completions`, v2 paths may need path-rewriting at the Kong level (rewrite `/v2/` → `/v1/` before forwarding) or LiteLLM must be configured to accept both prefixes. **This is a key implementation detail**: Kong must strip and rewrite the version prefix so LiteLLM always receives `/v1/` paths regardless of which version the client used. See `quickstart.md` for the `strip_path` and route `paths` configuration pattern.
