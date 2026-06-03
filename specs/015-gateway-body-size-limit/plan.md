# Implementation Plan: Gateway Request Body Size Limit

**Branch**: `015-gateway-body-size-limit` | **Date**: 2026-06-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/015-gateway-body-size-limit/spec.md`

## Summary

Install Kong's built-in `request-size-limiting` plugin at global scope with `allowed_payload_size: 10` (MB). This rejects any inference request whose body exceeds 10 MB with HTTP 413 before the payload is forwarded to any upstream service. Changes touch `scripts/seed-kong.sh` (Admin API seeding), `services/kong/kong.yml` (declarative fallback config), and `scripts/smoke-test.sh` (acceptance verification). No new services, containers, or code files are introduced.

## Technical Context

**Language/Version**: Bash (seed script); Kong 3.6 plugin DSL (declarative YAML)

**Primary Dependencies**: Kong 3.6 built-in `request-size-limiting` plugin — no additional packages required

**Storage**: N/A — plugin config is stateless; stored in Kong's PostgreSQL DB (DB mode) or in-memory (DB-less)

**Testing**: `curl` with oversized body (generated inline via `dd` or `python3 -c`) against Kong :8080

**Target Platform**: Kong gateway container inside Docker Compose network

**Performance Goals**: Rejection latency < 50 ms for payloads exceeding the limit (SC-003); zero added latency for compliant requests

**Constraints**: `allowed_payload_size` is in whole megabytes (integer); Kong measures actual received bytes for chunked transfers after buffering up to the limit

**Scale/Scope**: Global — all routes inherit the plugin without per-route configuration

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Request Flow Integrity | ✅ PASS | Enforcement added at Kong (edge) — the chain `Kong → Guardrails → LiteLLM` is untouched; rejections at Kong never reach downstream |
| II. Prompt Content Ephemeral | ✅ PASS | Rejection occurs before body is forwarded; no prompt content is logged — only metadata (request ID, route, size) |
| III. OpenAI API Compatibility | ✅ PASS | 413 is explicitly listed in the platform's structured error response table (§4.4); error body follows `{"error": ..., "message": ..., "detail": ...}` schema |
| IV. Defence in Depth — Kong Layer | ✅ PASS | Constitution §2.2 explicitly assigns "WAF / request size" to Kong. This feature fulfils a listed Kong responsibility |
| V. Falsifiable Acceptance Criteria | ✅ PASS | All criteria are `curl`-verifiable with deterministic HTTP status codes |

**Post-design re-check**: No design decision introduced after Phase 0 contradicts any principle. ✅

## Project Structure

### Documentation (this feature)

```text
specs/015-gateway-body-size-limit/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── 413-response.schema.json   ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
scripts/
├── seed-kong.sh         ← add create_request_size_plugin() + call in main()
└── smoke-test.sh        ← add oversized-payload test cases

services/kong/
└── kong.yml             ← add request-size-limiting to global plugins block
```

**Structure Decision**: Pure configuration change — no new services, no new directories. Modifications are confined to two scripts and one YAML file.

## Complexity Tracking

*No constitution violations. Section omitted.*
