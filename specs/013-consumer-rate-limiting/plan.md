# Implementation Plan: Per-Consumer Gateway Rate Limiting

**Branch**: `013-consumer-rate-limiting` | **Date**: 2026-05-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/013-consumer-rate-limiting/spec.md`

---

## Summary

Install Kong's built-in `rate-limiting` plugin at global scope with Redis-backed counters shared across all Kong instances. Each authenticated consumer identity is independently limited to 10 req/s, 300 req/min, and 10,000 req/hr. Throttled requests receive a 429 with `Retry-After` before reaching any downstream service. No new services or containers are introduced; all changes are additive to `scripts/seed-kong.sh`, `scripts/smoke-test.sh`, and `services/prometheus/rules.yml`.

---

## Technical Context

**Language/Version**: Bash 5 (seed-kong.sh, smoke-test.sh), YAML (Prometheus rules)

**Primary Dependencies**: Kong 3.6 OSS `rate-limiting` plugin, Redis 7.2 (existing, port 6379)

**Storage**: Redis at `redis:6379` — shared instance, same as LiteLLM prompt cache. Kong and LiteLLM use distinct key namespaces; no collision risk.

**Testing**: curl smoke tests (burst loop), Prometheus metric queries

**Target Platform**: Docker Compose (`core` profile — Kong + Redis already in `core`)

**Project Type**: Infrastructure configuration — no new service code, no new containers

**Performance Goals**: 429 response latency indistinguishable from 200 (Redis lookup adds <1 ms on local Docker network)

**Constraints**: Kong 3.6 OSS only (no Enterprise plugins). Fixed-window semantics (see research.md §1 — sliding window requires `rate-limiting-advanced` which is Enterprise-only).

**Scale/Scope**: One global plugin instance; counters keyed per consumer username in shared Redis.

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Pre-Design | Post-Design |
|---|---|---|
| I — Request Flow Integrity | ✅ | ✅ — plugin fires at Kong edge, before route dispatch to Guardrails |
| II — Prompt Content Ephemeral | ✅ | ✅ — counters and headers only; no content stored |
| III — OpenAI Compatibility | ✅ | ✅ — 429 error body uses `{"error","message","detail"}` schema (§4.4); `Retry-After` is standard HTTP |
| IV — Defence in Depth | ✅ | ✅ — rate limiting is §2.2's assigned Kong (layer 1) responsibility |
| V — Falsifiable Acceptance Criteria | ✅ | ✅ — all SC items verified via curl burst loop + Prometheus `rate(kong_http_requests_total[1m])` |

No violations. Complexity Tracking section omitted.

---

## Project Structure

### Documentation (this feature)

```text
specs/013-consumer-rate-limiting/
├── plan.md              ← this file
├── research.md          ← Phase 0 findings (window semantics, plugin config, metrics)
├── data-model.md        ← Rate-Limit Policy entity, Redis key schema, response headers
├── quickstart.md        ← Post-seed test guide
├── contracts/
│   └── kong-rate-limiting-plugin.md   ← Admin API contract
└── tasks.md             ← Phase 2 (/speckit-tasks — not yet created)
```

### Source Code (repository root)

```text
scripts/
├── seed-kong.sh         ← ADD: create_rate_limiting_plugin() + Prometheus plugin
└── smoke-test.sh        ← ADD: rate-limit burst test section

services/
└── prometheus/
    └── rules.yml        ← ADD: RateLimitStoreDown alert rule
```

**Structure Decision**: No new directories. All changes are additive to three existing files. Kong runs in DB mode; configuration is delivered via Admin API calls in `seed-kong.sh`, consistent with the established pattern for all other Kong plugins in this project.
