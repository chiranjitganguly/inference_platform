# Implementation Plan: Response Caching Layer

**Branch**: `007-response-caching` | **Date**: 2026-05-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/007-response-caching/spec.md`

## Summary

Enable LiteLLM's built-in Redis response cache so that non-streaming requests with identical model, messages array, and temperature return stored responses in under 10 ms with zero provider calls. Redis 7.2-alpine is added to the core Docker Compose profile at port 6379 (cache) and port 6380 (batch queue). LiteLLM cache_params are extended with `redis_host`, `redis_port`, `namespace: llm_cache`, and `ttl` via environment variable. Streaming bypass is already enforced by feature 006's `supported_call_types` restriction. Cache hits increment the native `litellm_cache_hit_count` Prometheus counter and do not create Phoenix spans or Langfuse traces. No new services or custom code are required — this is a pure configuration and test delivery.

## Technical Context

**Language/Version**: YAML (LiteLLM config), Bash (smoke test), Python 3.11 (contract tests)

**Primary Dependencies**: LiteLLM v1.52.0 (built-in Redis cache client), Redis 7.2-alpine

**Storage**: Redis 7.2-alpine — two instances:
- `redis-cache` port 6379: response cache, maxmemory 256 MB, allkeys-lru eviction, no persistence
- `redis-queue` port 6380: batch job queue (separate instance, noeviction — unchanged from constitution §3)

**Testing**: pytest + requests (contract tests in `tests/contract/test_caching.py`), curl (smoke test extensions in `scripts/smoke-test.sh`)

**Target Platform**: Docker Compose core profile, macOS OrbStack (development)

**Performance Goals**: Cache hit round-trip < 10 ms end-to-end at the Kong gateway

**Constraints**: Cache must not store streaming responses (`stream: true`); cache hits must not invoke Phoenix or Langfuse callbacks; Redis core profile memory addition must stay within ~620 MB core budget

**Scale/Scope**: Single-node Redis; no clustering; local development target only in this phase

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I — Request Flow Integrity | ✅ PASS | Cache operates entirely within LiteLLM; Kong → Guardrails → LiteLLM chain is unchanged. No shortcut is created — cache lookup and cache write both happen inside the LiteLLM process. |
| II — Prompt Content Ephemeral | ⚠️ ANNOTATED PASS | §2.4 prohibits response text in Redis; §2.2 explicitly assigns "Prompt/response caching — LiteLLM + Redis — Content-aware" to LiteLLM + Redis. §2.2 is the more specific rule and governs here. All cached responses are produced from PII-redacted prompts (§6.2 — Presidio runs before LiteLLM). Cache store is internal to the Docker network and not queryable by operators. See Complexity Tracking. |
| III — OpenAI API Compatibility | ✅ PASS | Cache hits return the same OpenAI-format JSON body as provider responses. Cache status is conveyed via HTTP response header (`x-litellm-cache-hit: True/False`) — JSON body schema is unchanged. |
| IV — Defence in Depth | ✅ PASS | Auth (Kong), OPA policy, and Guardrails PII/injection scans all execute before any cache lookup. Cache is within the LiteLLM layer. |
| V — Falsifiable Acceptance Criteria | ✅ PASS | All success criteria are Prometheus queries, curl commands with timing assertions, or counter deltas. |
| Memory Budget | ✅ PASS | Two Redis 7.2-alpine containers with 256 MB maxmemory each. Idle Redis ~10–20 MB RSS; peak within 256 MB cap. Core profile budget ~620 MB. |

## Project Structure

### Documentation (this feature)

```text
specs/007-response-caching/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── openapi.yaml     # Cache status header addition to existing endpoint
└── tasks.md             # Phase 2 output (/speckit-tasks — not created here)
```

### Source Code Changes

```text
docker-compose.yml                         # Add redis-cache + redis-queue services (core profile)
services/litellm/config.yaml               # Add redis_host, redis_port, namespace, ttl to cache_params
.env.example                               # Add LITELLM_CACHE_TTL
tests/contract/test_caching.py             # NEW — cache hit/miss/streaming/Prometheus tests
scripts/smoke-test.sh                      # Extend with cache-hit and cache-miss probes
```

## Complexity Tracking

| Item | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Response text stored in Redis (apparent §2.4 tension) | Cache correctness requires storing the full response body to replay it on hit | Storing only a hash or status flag cannot replay the response; §2.2 explicitly authorises LiteLLM+Redis for content-aware caching; all stored content is PII-redacted and not operator-queryable |
| Two Redis instances (6379 + 6380) | Separation of concerns: cache uses allkeys-lru eviction (may evict under pressure); batch queue uses noeviction (must never lose jobs) | A single Redis instance with one eviction policy cannot satisfy both requirements simultaneously |
