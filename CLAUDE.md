# CLAUDE.md — AI Inference Platform

This file is read automatically by Claude Code at the start of every session.
It gives you persistent project context so you never need to be re-briefed.

---

## What this project is

A unified LLM inference gateway serving proprietary models (OpenAI, Anthropic,
Google, Cohere) via a single OpenAI-compatible API. Built for an 8GB Mac using
Docker Compose profiles, with a migration path to GCP GKE.

**Constitution:** `docs/constitution.md` — read it before any architectural
decision. The invariants there are non-negotiable.

**Phase plan:** 10 phases, one feature at a time via SpecKit.
Current phase and active feature are tracked in `docs/progress.md`.

<!-- SPECKIT START -->
**Active feature plan:** `specs/013-consumer-rate-limiting/plan.md`
<!-- SPECKIT END -->

---

## Repository layout (memorise this)

```
inference-platform/
├── CLAUDE.md                        # This file
├── constitution.md                  # Architectural invariants — never violate
├── Makefile                         # All commands — never use docker compose directly
├── .env.example                     # Committed — variable names only, no values
├── .env                             # Gitignored — real secrets
├── docker-compose.yml               # All services with Docker Compose profiles
│
├── services/
│   ├── litellm/config.yaml          # Model catalogue + routing + callbacks
│   ├── kong/                        # Kong declarative config
│   ├── guardrails/main.py           # FastAPI pre/post inference pipeline
│   ├── batch-worker/main.py         # Async batch inference processor
│   ├── portal-backend/main.py       # Cost estimator + catalogue API
│   ├── platform-ui/                 # Next.js 15 + React 19 platform UI
│   ├── keycloak/realm-export.json   # Keycloak realm — auto-imported at start
│   ├── opa/policies/inference.rego  # ABAC policy rules
│   ├── llm-guard/scanners.yml       # LLM Guard scanner config
│   ├── prometheus/                  # prometheus.yml + rules.yml + alertmanager.yml
│   ├── grafana/                     # provisioning/ + dashboards/ (6 dashboards)
│   ├── loki/loki-config.yml
│   ├── otel/otel-collector.yml
│   └── mlflow/
│
├── scripts/
│   ├── setup-mac.sh                 # First-time setup
│   ├── init-db.sql                  # Creates all 6 PostgreSQL databases
│   ├── seed-kong.sh                 # Bootstrap Kong via Admin API
│   ├── seed-vault.sh                # Bootstrap Vault secrets
│   ├── smoke-test.sh                # Curl all endpoints — run after every change
│   ├── register-models.py           # MLflow model registration
│   ├── prompt-register.py           # Langfuse prompt versioning
│   ├── prompt-evaluate.py           # Langfuse prompt evaluation pipeline
│   ├── prompt-promote.py            # Langfuse prompt stage promotion
│   └── fairness-check.py           # Weekly bias monitoring
│
├── charts/                          # Helm charts — one per service (Phase 10)
├── deploy/                          # GCP Cloud Deploy + Kustomize overlays
├── .github/workflows/               # GitHub Actions CI/CD
├── tests/smoke/ tests/load/ tests/contract/
└── docs/
    ├── constitution.md
    ├── progress.md                  # Current phase + active feature
    ├── ports.md                     # Port registry — canonical reference
    └── adr/                         # Architecture Decision Records
```

---

## Port registry (committed to memory)

| Service | Port | Notes |
|---|---|---|
| Kong proxy | **8080** | All client traffic — the only public port |
| Kong admin | 8001 | Localhost only |
| LiteLLM | 4000 | **Internal only** after Phase 02 — no host binding |
| Redis cache | 6379 | |
| Redis queue | 6380 | Batch jobs — separate instance, noeviction |
| PostgreSQL | 5432 | Shared: litellm, keycloak, mlflow, kong, phoenix, langfuse |
| Prometheus | 9090 | |
| Grafana | 3000 | 6 dashboards |
| Loki | 3100 | Audit trail + structured logs |
| OTel HTTP | 4318 | Telemetry ingestion |
| OTel gRPC | 4317 | |
| Alertmanager | 9093 | |
| Jaeger UI | 16686 | Non-LLM service traces |
| **Phoenix Arize** | **6006** | LLM traces + Prometheus /metrics |
| **Langfuse server** | **3002** | Prompt management + REST API |
| Keycloak | 8083 | SSO admin UI |
| OPA | 8181 | Policy engine |
| Vault | 8200 | Secrets (dev mode locally) |
| Presidio analyzer | 5002 | |
| Presidio anonymizer | 5003 | |
| LLM Guard | 8087 | |
| Guardrails service | 8088 | |
| MLflow | 5000 | Model registry |
| Batch worker | 8091 | |
| Swagger UI | 8090 | API explorer |
| Portal backend | 8092 | |
| Platform UI | 3001 | Next.js — 3000 taken by Grafana |

---

## Request flow — never deviate from this

```
Client
  → Kong :8080          (auth, rate limit, WAF, correlation ID, W3C traceparent)
  → Guardrails :8088    (PII redaction, injection scan, OPA policy, toxicity)
  → LiteLLM :4000       (model routing, BYOK, caching, fallback, spend tracking)
  → Cloud LLM API
```

On the way back:
- LiteLLM writes to Redis cache
- Guardrails runs post-inference scan (toxicity, relevance, sensitive data)
- Kong adds X-Platform and X-API-Version response headers

LiteLLM callbacks emit to **three backends simultaneously**:
- `arize_phoenix` → Phoenix Arize :6006 (LLM spans)
- `langfuse` → Langfuse :3002 (prompt-linked traces)
- `prometheus` → Prometheus :9090 (metrics)

---

## Make commands (always use make — never raw docker compose)

```bash
make up-core          # Core: LiteLLM + Kong + Redis×2 + Postgres + Prometheus + Grafana
make up-obs           # Core + Loki + OTel + Alertmanager + Jaeger + Phoenix + Langfuse
make up-auth          # Core + Keycloak + OPA + Vault
make up-safety        # Core + Presidio + LLM Guard + Guardrails service
make up-gov           # Core + MLflow + Jaeger
make up-portal        # Core + Swagger UI + Portal backend + Platform UI
make up-all           # Everything — watch memory with: make stats
make down             # Stop all
make restart svc=X    # Restart one service
make logs svc=X       # Tail logs
make ps               # Service status
make stats            # Live memory per container
make smoke            # Run smoke-test.sh against :8080
make seed-kong        # Bootstrap Kong after up-core
make seed-vault       # Bootstrap Vault after up-auth
```

---

## LiteLLM config.yaml — key sections to know

**Callbacks (must always be all three):**
```yaml
litellm_settings:
  callbacks: [arize_phoenix, langfuse, prometheus]
```

**Required environment variables for callbacks:**
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://arize-phoenix:6006/v1/traces
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://langfuse-server:3000
```

**Model catalogue models (9 LLMs + 2 embedding):**
`gpt-4o`, `gpt-4o-mini`, `gpt-4.1`, `o4-mini` (OpenAI)
`claude-sonnet`, `claude-haiku` (Anthropic)
`gemini-flash`, `gemini-pro` (Google)
`command-r-plus` (Cohere)
`text-embedding-3-small`, `text-embedding-3-large` (embeddings)

**Fallback chains:**
- `gpt-4o` → `[claude-sonnet, gemini-pro]`
- `gpt-4o-mini` → `[claude-haiku, gemini-flash]`
- `claude-sonnet` → `[gpt-4o, gemini-pro]`
- `gemini-flash` → `[gpt-4o-mini, claude-haiku]`

---

## Hard constraints (from constitution.md)

1. **Prompt content is never persisted.** Not in Loki, not in Phoenix spans,
   not in Langfuse, not in PostgreSQL, not in Redis. Audit entries contain
   metadata only: timestamp, event_type, request_id, key_hash, model,
   pii_redacted count, scanner_blocked.

2. **LiteLLM port 4000 has no host binding after Phase 02.** All traffic goes
   through Kong :8080.

3. **No `kubectl apply` in production.** All changes via ArgoCD and GitOps.

4. **No long-lived GCP credentials.** GitHub Actions uses OIDC Workload Identity
   Federation only.

5. **All images are multi-platform** (`linux/amd64` + `linux/arm64`).

6. **CRITICAL CVEs block merge.** Trivy scan is a required CI check.

7. **Prompts are in Langfuse, not in code.** Use
   `langfuse.get_prompt(name, version="production")` at runtime.

8. **All dashboards in Grafana.** Don't send operators to Phoenix or Langfuse
   UI for routine monitoring. Only for deep trace inspection.

---

## Current tech stack (locked — don't suggest upgrades without an ADR)

**Python services:** FastAPI, httpx (async), opentelemetry-sdk,
openinference-instrumentation, langfuse, presidio-analyzer, ruff + mypy

**Platform UI:** Next.js 15 (App Router), React 19, Tailwind CSS v4,
shadcn/ui, TanStack Query v5, Recharts 2.x, Zustand 5, next-auth v5,
TypeScript 5.5 strict, Zod 3, Lucide React

**CI/CD:** GitHub Actions, docker/build-push-action@v6,
aquasecurity/trivy-action@v0.28.0, sigstore/cosign-installer@v3,
google-github-actions/auth@v2, Skaffold 2.x, ArgoCD v2.12, Helm 3.x,
Google Cloud Deploy

---

## Code standards

### Python
- Type annotations on all function signatures — no bare `def foo(x)`
- `ruff check` and `mypy --strict` must pass with zero errors
- `httpx.AsyncClient` for all HTTP — never `requests`
- FastAPI only — no Flask, no Django
- OpenInference span kinds for LLM instrumentation:
  `LLM`, `CHAIN`, `GUARDRAIL`, `RETRIEVER`, `EMBEDDING`

### TypeScript / React
- `tsc --noEmit` zero errors — no `any` types
- Zod schema for every API response shape
- React Server Components by default — Client Components only when needed
- Tailwind v4 utility classes only — no inline styles
- All data fetching via TanStack Query v5 — no raw `useEffect` + `fetch`

### Docker
- Non-root user in all custom images
- Base images pinned to specific tags (except Phoenix and Langfuse — latest only)
- Multi-stage builds for all production images
- Healthchecks on every service

### Secrets
- No values in `.env.example` — names only
- No values in `docker-compose.yml` — reference `${VAR}` only
- Sensitive values in Vault at runtime

---

## How to run checks before committing

```bash
# Python services
ruff check services/guardrails/ services/batch-worker/ services/portal-backend/
mypy services/guardrails/ --ignore-missing-imports

# Platform UI
cd services/platform-ui && pnpm tsc --noEmit && pnpm lint

# Smoke test (core stack must be running)
make smoke

# Memory check
make stats
```

---

## Acceptance criteria format

Every feature's done condition must be a deterministic test:
- `curl` command with expected HTTP status and response fields
- `docker stats` snapshot showing memory within budget
- Prometheus query returning expected metric value
- Loki query returning expected log fields

**Never accept:** "works correctly", "looks good", "seems to work". Every
criterion must be falsifiable.

---

## Grafana datasources (6 total)

| Datasource | Type | URL | Auth |
|---|---|---|---|
| Prometheus | prometheus | http://prometheus:9090 | none |
| Loki | loki | http://loki:3100 | none |
| Langfuse | marcusolsson-json-datasource | http://langfuse-server:3000/api | Basic: LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY |
| Jaeger | jaeger | http://jaeger:16686 | none |
| Alertmanager | alertmanager | http://alertmanager:9093 | none |

Phoenix metrics come through the existing Prometheus datasource (phoenix_ prefix).
No separate Phoenix datasource needed.

---

## Prompt governance workflow

```
Register → draft label → evaluate (≥0.80 correctness) → staging → promote → production
                                     ↓ fail
                              blocked — fix and re-evaluate
```

Scripts:
```bash
python scripts/prompt-register.py   # Create/update prompt version in Langfuse
python scripts/prompt-evaluate.py   # Run evaluation against golden dataset
python scripts/prompt-promote.py    # Promote to production (blocks if score < 0.80)
```

MLflow model card must include:
```python
tags={"current_prompt_version": "prompt-name:N"}
```

---

## Model governance workflow

```
Register → None → Staging → Production (archive previous) → rollback if needed
```

```bash
python scripts/register-models.py          # Register model + model card
python scripts/register-models.py rollback # Restore previous Production version
python scripts/fairness-check.py          # Weekly bias monitoring
```

---

## PostgreSQL databases (all on port 5432)

| Database | Used by |
|---|---|
| `litellm` | LiteLLM spend logs, virtual keys, model config |
| `keycloak` | Keycloak users, realms, sessions |
| `mlflow` | Model registry, experiments, runs |
| `kong` | Kong services, routes, consumers, plugins |
| `phoenix` | Phoenix Arize LLM traces and spans |
| `langfuse` | Langfuse prompt versions, evaluations, traces |

All created by `scripts/init-db.sql` on first Postgres start.

---

## When you are unsure

1. Check `docs/constitution.md` — if it is covered there, follow it exactly
2. Check `docs/adr/` — if a decision was made before, don't relitigate it
3. Check `docs/progress.md` — confirm which phase and feature is active
4. Run `make smoke` — if it fails, fix it before proceeding
5. Run `make stats` — if memory exceeds ~4.2 GB, find what to stop
6. Ask: "does this bypass the Kong → Guardrails → LiteLLM chain?" — if yes, do not do it