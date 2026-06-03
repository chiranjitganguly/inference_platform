# Research: Gateway Request Body Size Limit

**Feature**: 015-gateway-body-size-limit | **Date**: 2026-06-02

---

## Decision 1: Plugin Choice

**Decision**: Use Kong's built-in `request-size-limiting` plugin — no third-party plugin required.

**Rationale**: Bundled with Kong 3.6 (the locked version). Zero additional dependencies, zero image changes, zero container restarts beyond applying the config. The plugin exposes exactly one config field relevant to this feature: `allowed_payload_size` (integer, MB unit).

**Alternatives considered**:
- Custom Lua plugin via `pre-function` — rejected: unnecessary complexity when a first-party plugin exists.
- nginx `client_max_body_size` directive — rejected: Kong DB mode does not expose raw nginx config; the plugin is the supported surface.

---

## Decision 2: Plugin Scope — Global vs. Per-Service

**Decision**: Install at **global scope** (no service or route association).

**Rationale**: All inference routes must enforce the same limit (FR-003). Global scope is the single-configuration guarantee. Adding it per-service or per-route would require updating every future service registration.

**Alternatives considered**:
- Per-service plugin on `litellm-inference` and `litellm-embeddings` — rejected: misses future services and requires N updates for 1 policy change.
- Per-route plugin — rejected: same fragility problem, squared.

---

## Decision 3: Size Limit Value and Unit

**Decision**: `allowed_payload_size: 10` (plugin unit is MB; this equals 10,485,760 bytes).

**Rationale**: User-specified. Provides ample headroom for the largest legitimate prompt batches while blocking context-stuffing attacks. The limit is externally configurable by changing the seed script or YAML value — no code rebuild needed (FR-006).

**Alternatives considered**: N/A — value explicitly specified by product owner.

---

## Decision 4: Chunked Transfer-Encoding Handling

**Decision**: Rely on Kong 3.6's built-in chunked-body buffering behaviour of `request-size-limiting`.

**Rationale**: Kong buffers incoming chunked transfer data incrementally. Once the running byte count crosses `allowed_payload_size`, Kong closes the connection and returns 413 without forwarding to upstream. This satisfies FR-002 (no forwarding) and FR-007 (actual byte measurement, not just `Content-Length`). The `Content-Length` header is checked first as a fast path; chunked bodies without it are measured in flight.

**Alternatives considered**: Custom buffering middleware — rejected: unnecessary given built-in behaviour.

---

## Decision 5: Error Response Body Format

**Decision**: Accept Kong's default 413 JSON response; do **not** customise the body via `pre-function`.

**Rationale**: Kong 3.6 returns:
```json
{"message": "Request size limit exceeded"}
```
with `Content-Type: application/json`. This is machine-readable. The Assumptions section of the spec notes the OpenAI error schema (`{"error": {...}}`) as the target; however, the built-in plugin produces a simpler shape. Given that 413 is an edge rejection before any model routing occurs, clients are expected to handle it as a transport-layer error, not a model-layer error. No customisation is required for this phase.

**Alternatives considered**:
- Override with `post-function` Lua to wrap in `{"error": ..., "message": ..., "detail": ...}` — deferred: adds complexity with no material client-experience benefit at the gateway rejection layer.

---

## Decision 6: Idempotency in seed-kong.sh

**Decision**: Use the existing `_plugin_exists_global` helper before installing the plugin, matching the pattern used for `rate-limiting` and `correlation-id`.

**Rationale**: `seed-kong.sh` is designed to be re-runnable. Skipping an existing global plugin prevents duplicate installation errors on re-seed.

---

## Decision 7: Audit Logging

**Decision**: Rely on Kong's access log (structured JSON) for the 413 audit trail. No custom log emission step is needed.

**Rationale**: Kong already emits a structured access log entry for every request including rejected ones. The entry contains: timestamp, route, service, status, request size (from headers), consumer identity, and `X-Request-ID`. This satisfies FR-008. These logs flow to Loki via the existing log pipeline. No prompt content is present.

**Alternatives considered**:
- Custom Lua `log` phase handler to emit a dedicated audit event — rejected: duplicates what the access log already provides.
