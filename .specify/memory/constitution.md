<!--
SYNC IMPACT REPORT
==================
Version change: template (unversioned) → 1.0.0
Migration: First population of .specify/memory/constitution.md from root constitution.md (v1.0)

Modified principles:
  - All sections NEW (populated from root constitution.md v1.0)

Added sections:
  - Core Principles (5 principles derived from 12-section root constitution)
  - Technology Stack & Architecture Invariants
  - Code Quality, Deployment & Governance Standards
  - Governance

Removed sections: N/A — initial fill

Templates reviewed:
  - .specify/templates/plan-template.md  ✅ No changes needed; "Constitution Check" gate is
      generic and will correctly reference this document's 5 principles at plan time.
  - .specify/templates/spec-template.md  ✅ No changes needed; FR/SC format is compatible with
      the platform's falsifiable acceptance-criteria principle.
  - .specify/templates/tasks-template.md ✅ No changes needed; user-story-driven structure aligns
      with the platform's independent-deliverability mandate.
  - No commands/ directory exists — no command files to review.

Follow-up TODOs:
  - TODO(RATIFICATION_DATE): Original adoption date unknown; mark with actual date when confirmed.
  - Canonical locked versions (Section 3 of root constitution) are referenced here by component
      family only. The authoritative version table lives in constitution.md at the repo root and
      should be kept in sync with docker-compose files.
-->

# AI Inference Platform Constitution

> This document is the single source of truth for all architectural decisions,
> design principles, and non-negotiable constraints across all 10 phases of the
> AI Inference Platform. Feed this to every SpecKit session before specifying
> any feature: `/speckit.constitution constitution.md`

---

## How to Read This Document

| Part | Purpose | Audience |
|---|---|---|
| **Part I — Quick Reference** | Five non-negotiable principles + summary constraints | SpecKit sessions, PR reviews, onboarding |
| **Part II — Full Specification** | Complete 12-section detailed constitution | Deep implementation work, ADR authoring |

Both parts are authoritative. Part I is derived from Part II; when in doubt, Part II governs.

---

# Part I — SpecKit Quick Reference

## Core Principles

### I. Request Flow Integrity (NON-NEGOTIABLE)

Every inference request MUST traverse exactly this chain without shortcutting:

```
Client → Kong :8080 → Guardrails :8088 → LiteLLM :4000 → Cloud LLM API
```

- Kong is the ONLY externally accessible port. All other service ports are internal to the
  Docker network.
- The Guardrails service is the ONLY caller of LiteLLM. Application code MUST NOT call LiteLLM
  directly.
- Performance improvements MUST be achieved by optimising within the chain, never by bypassing it.
- Kong and LiteLLM responsibilities MUST NOT overlap. The boundary table in Part II §2.2
  is authoritative.

**Rationale**: Bypassing any layer breaks the security, observability, and cost-attribution
guarantees that the entire platform is built on.

### II. Prompt Content is Ephemeral (NON-NEGOTIABLE)

Prompt text, response text, and image data MUST NEVER appear in:

- Any Loki log entry or SIEM export
- Any Phoenix Arize span attribute or tag
- Any Langfuse trace field queryable by operators
- Any PostgreSQL table column, Redis value, or Grafana panel

Audit trail entries contain ONLY: `timestamp`, `event_type`, `request_id`, `key_hash`,
`model_name`, `pii_entity_count`, `scanner_blocked`. Nothing else.

PII MUST be redacted by Presidio as the FIRST step in the Guardrails pipeline. By the time a
prompt reaches LiteLLM, all detected PII has been replaced with `<REDACTED>`.

**Rationale**: Regulatory compliance (GDPR, data residency). Storing inference content creates
audit, liability, and breach-scope risks that the platform explicitly avoids.

### III. OpenAI API Compatibility is Mandatory

Every inference endpoint MUST accept and return the OpenAI API schema. Existing integrations
using the OpenAI Python SDK, LangChain, LlamaIndex, or any OpenAI-compatible client MUST work
without code changes.

- `/v1` is the stable surface and MUST NOT be broken. Breaking changes go to `/v2` with a
  minimum 6-month deprecation notice.
- Deprecated endpoints MUST return a `Deprecation` header with the removal date.
- Every response MUST include `X-Request-ID`, `X-Platform: inference-platform`, and
  `X-API-Version: 1` headers attached by Kong.
- Error responses MUST use the structured `{ "error", "message", "detail" }` schema with
  semantically correct HTTP status codes (400/401/403/404/413/429/451/503).

**Rationale**: Application teams MUST be able to switch to this platform without changing a
single line of client code.

### IV. Defence in Depth — Three Enforcement Layers

Every request MUST pass through three independent enforcement layers in order:

1. **Kong (edge)** — authentication, rate limiting (RPM/RPS), request size validation
2. **OPA (policy)** — authorisation, model-level ABAC, team-level model restrictions
3. **Guardrails (content)** — PII detection/redaction, prompt injection, toxicity scanning

A request blocked at any layer MUST NOT proceed to the next layer. These layers are
additive — passing layer N does not grant trust at layer N+1.

All credentials MUST be stored in HashiCorp Vault. `.env` files are gitignored. CI/CD MUST
authenticate to GCP via OIDC Workload Identity Federation — no long-lived service account keys.
All container images MUST be signed by CI with Cosign keyless signing.

**Rationale**: A single enforcement mechanism creates a single point of failure. Each layer
catches different attack vectors; together they provide compliance-grade defence.

### V. Falsifiable Acceptance Criteria

Every feature's completion condition MUST be expressible as a deterministic, independently
verifiable test — one of:

| Form | Example |
|---|---|
| `curl` command | Expected HTTP status + response body |
| `docker stats` snapshot | Memory within the defined budget |
| Prometheus query | Returns expected metric value |
| Loki query | Returns expected log fields |

Acceptance criteria of the form "works correctly", "looks good", or "should behave" are
REJECTED. Every criterion MUST be falsifiable before a feature is considered done.

**Rationale**: Vague criteria produce shipping confidence gaps and production regressions. The
platform's multi-layer observability stack makes deterministic verification inexpensive.

---

## Technology Stack & Architecture Invariants

### Observability — Three-Layer Separation

- **Prometheus + Grafana** — metrics: RPS, TTFT, error rate, cost, token counts.
- **Phoenix Arize** — LLM-specific traces: spans, token distribution, model quality, agent
  chains, guardrail overlap. Receives via `arize_phoenix` callback from LiteLLM.
- **Langfuse** — prompt management, versioning, and evaluation scores. Receives via `langfuse`
  callback from LiteLLM.
- **Jaeger** — non-LLM service-to-service distributed traces ONLY.
- **Loki** — structured logs and immutable audit trail.
- ALL dashboards MUST live in Grafana (6 dashboards defined in Part II §7.3).
  Operators MUST NOT need Phoenix or Langfuse UIs for routine monitoring.

### Memory Budget (8 GB Mac Development)

The platform MUST function with the `core` Docker Compose profile alone for development.
No feature may require multiple profiles simultaneously to pass acceptance criteria.

| Profile | Limit |
|---|---|
| core only | ~620 MB |
| core + obs | ~2.15 GB |
| core + obs + auth | ~2.81 GB |
| All profiles | ~4.16 GB |

### Locked Technology Versions

Component versions are locked in Part II §3 and MUST NOT be upgraded without a documented ADR.
Key families: LiteLLM (main-v1.52.0), Kong (3.6), Keycloak (24.0.3), OPA (0.63.0),
Vault (1.16.1), Presidio, LLM Guard, Next.js 15 / React 19 / Tailwind v4.

---

## Code Quality, Deployment & Governance Standards

### Code Quality Gates

**Python services**: Type annotations on all function signatures; `ruff` zero warnings;
`mypy` strict mode zero errors; FastAPI for all HTTP services; `httpx` for async HTTP client;
OpenTelemetry SDK with OpenInference conventions for LLM spans.

**TypeScript (Platform UI)**: TypeScript 5.5 strict mode, no `any`; `tsc --noEmit` zero errors;
Zod schemas for all API response shapes; React 19 Server Components by default; Tailwind v4
utility classes only — no inline styles.

**Docker images**: Multi-platform (`linux/amd64` + `linux/arm64`); Trivy scan zero CRITICAL CVEs
before merge; pinned base image tags (no `:latest` except Phoenix/Langfuse); non-root user in
all custom images.

### Model & Prompt Governance

Every model MUST be registered in MLflow with a model card (provider, tier, context window,
pricing, GDPR compliance, current prompt version) before serving traffic.

Every system prompt MUST be versioned in Langfuse — no hardcoded prompts in code or config.
Promotion to Production requires passing automated evaluation:

- **Models** — pre-production evaluation harness
- **Prompts** — ≥ 0.80 correctness score on a golden dataset (minimum 20 examples)

Rollback MUST be achievable in a single script call and MUST revert both model version and
linked prompt version atomically.

### GitOps & Deployment

Local development: OrbStack + Docker Compose profiles. Production: GCP GKE via ArgoCD.
Manual `kubectl apply` is PROHIBITED in production. ArgoCD self-heals manual drift within
3 minutes. Production releases MUST use canary progression: `10% → 25% → 50% → 100%`, with
automatic rollback on verification failure. Manual rollback via `gcloud deploy rollback` MUST
complete within 5 minutes.

---

## Governance

This constitution is the single source of truth for all architectural decisions and
non-negotiable constraints across all 10 phases of the AI Inference Platform.

**Amendment procedure**: Any change to an architecture invariant (Principles I–IV above) requires
a documented Architecture Decision Record (ADR) committed to the repository before implementation
begins. Amendments to code quality or deployment standards require a PR with explicit sign-off
from a platform operator.

**Versioning policy** (semantic):

| Bump | Trigger |
|---|---|
| MAJOR | Backward-incompatible removal or redefinition of a principle or invariant |
| MINOR | New principle, section added, or materially expanded guidance |
| PATCH | Clarifications, wording fixes, non-semantic refinements |

**Compliance review**: All PRs MUST verify compliance with Principles I–V at the
"Constitution Check" gate in the plan template before Phase 0 research begins, and re-check
after Phase 1 design.

**Scope**: Items deliberately left to individual feature specs (PromQL queries, Rego rules,
Langfuse dataset structure, Grafana panel layout, Helm resource limits) are enumerated in
Part II §12.

---

# Part II — Full Specification

## 1. Project Identity

**What this platform is:**
A unified inference gateway that gives internal engineering teams access to
proprietary LLMs (OpenAI, Anthropic, Google, Cohere) via a single
OpenAI-compatible API, with enterprise security, observability, and governance
built in. Open-source LLM hosting (vLLM on GPU) is a deferred addition in
Phase 09+.

**Who uses it:**

| Role | Responsibility |
|---|---|
| Application developers | Call models via API without managing provider credentials |
| Data scientists | Run batch inference and prompt evaluations |
| Platform operators | Monitor health, cost, and model quality |
| Compliance officers | Audit all inference events and manage data subject requests |
| Security administrators | Control access, enforce policies, rotate secrets |

**What it is not:**
This platform does not train models, does not store prompts or responses in any
persistent log, and does not expose any raw LLM provider credentials to
application code.

---

## 2. Architecture Invariants

These decisions are final. No feature may contradict them.

### 2.1 Request Flow — Never Deviate from This Chain

```
Client → Kong :8080 → Guardrails :8088 → LiteLLM :4000 → Cloud LLM API
```

- **Kong** is the only externally accessible port (8080). All other service
  ports are internal to the Docker network after Phase 02.
- **Guardrails service** sits between Kong and LiteLLM. It runs all pre and
  post-inference pipelines. It is the only service that calls LiteLLM directly.
- **LiteLLM** is the model proxy. It is never called by application code
  directly — only by the guardrails service or the batch worker.
- No shortcutting this chain for performance. If a feature needs lower latency,
  optimise within the chain — do not bypass it.

### 2.2 Kong + LiteLLM — Responsibilities Never Overlap

| Responsibility | Owner | Reason |
|---|---|---|
| Rate limiting (RPM/RPS) | Kong | Edge concern — reject before model routing |
| Token/spend budget limits | LiteLLM virtual keys | Needs token counts from provider |
| API key authentication | Kong key-auth plugin | Edge reject before hitting proxy |
| JWT / SSO validation | Kong jwt + Keycloak | Edge concern |
| WAF / request size | Kong plugin | Edge concern |
| X-Request-ID + W3C traceparent | Kong plugin | Attached at edge |
| API versioning /v1 /v2 | Kong routes | Path routing at edge |
| Model routing + fallback | LiteLLM router | Model-aware decision |
| BYOK key mapping | LiteLLM virtual keys | Maps team key to provider key |
| Prompt/response caching | LiteLLM + Redis | Content-aware |
| Cost attribution | LiteLLM /v1/spend | Token-aware calculation |

### 2.3 Observability Stack — Three-Layer Separation

- **Prometheus + Grafana** — metrics (RPS, TTFT, error rate, cost, token counts)
- **Phoenix Arize** — LLM-specific traces (spans, token distribution, model
  quality, agent chains, guardrail overlap). Receives via `arize_phoenix` callback
  from LiteLLM. Grafana reads Phoenix metrics via Prometheus scrape.
- **Langfuse** — prompt management, versioning, and evaluation scores. Receives
  via `langfuse` callback from LiteLLM. Grafana reads Langfuse data via JSON
  datasource plugin.
- **Jaeger** — non-LLM service-to-service distributed traces only.
- **Loki** — structured logs and immutable audit trail.
- **ALL dashboards live in Grafana.** Operators must not need to open Phoenix
  or Langfuse UIs for routine monitoring — only for deep trace inspection.

### 2.4 Prompt Content is Never Persisted

> **This is a hard constraint with no exceptions.**

Prompt text, response text, and image data must never appear in:

- Any Loki log entry
- Any Phoenix span attribute or tag
- Any Langfuse trace field that is queryable by operators
- Any PostgreSQL table column
- Any Redis value
- Any Grafana panel
- Any SIEM export

Audit trail entries contain only: `timestamp`, `event_type`, `request_id`, `key_hash`,
`model_name`, `pii_entity_count`, `scanner_blocked`. Nothing else.

---

## 3. Technology Choices — Locked Per Phase

These are the canonical versions. Do not upgrade without a documented ADR.

### Core Inference

| Component | Image / Version | Port |
|---|---|---|
| LiteLLM Proxy | `ghcr.io/berriai/litellm:main-v1.52.0` | 4000 (internal) |
| Redis cache | `redis:7.2-alpine` | 6379 |
| Redis queue | `redis:7.2-alpine` | 6380 |
| PostgreSQL | `postgres:16-alpine` | 5432 |

### Gateway

| Component | Image / Version | Port |
|---|---|---|
| Kong | `kong:3.6` | 8080 (public), 8001 (admin, localhost only) |

### Observability

| Component | Image / Version | Port |
|---|---|---|
| Prometheus | `prom/prometheus:v2.49.1` | 9090 |
| Grafana | `grafana/grafana:10.3.3` | 3000 |
| Loki | `grafana/loki:2.9.5` | 3100 |
| OTel Collector | `otel/opentelemetry-collector-contrib:0.97.0` | 4317, 4318 |
| Alertmanager | `prom/alertmanager:v0.27.0` | 9093 |
| Jaeger | `jaegertracing/all-in-one:1.55` | 16686, 14250 |
| Phoenix Arize | `arizephoenix/phoenix:latest` | 6006 |
| Langfuse server | `langfuse/langfuse:3` | 3002 |
| Langfuse worker | `langfuse/langfuse-worker:3` | — |

### Auth & Security

| Component | Image / Version | Port |
|---|---|---|
| Keycloak | `quay.io/keycloak/keycloak:24.0.3` | 8083 |
| OPA | `openpolicyagent/opa:0.63.0` | 8181 |
| Vault | `hashicorp/vault:1.16.1` | 8200 |

### Safety

| Component | Image / Version | Port |
|---|---|---|
| Presidio analyzer | `mcr.microsoft.com/presidio-analyzer` | 5002 |
| Presidio anonymizer | `mcr.microsoft.com/presidio-anonymizer` | 5003 |
| LLM Guard | `ghcr.io/protectai/llm-guard-api:latest` | 8087 |
| Guardrails service | Custom FastAPI (built from source) | 8088 |

### Governance

| Component | Image / Version | Port |
|---|---|---|
| MLflow | `ghcr.io/mlflow/mlflow:v2.11.3` | 5000 |

### Platform UI

| Framework | Version |
|---|---|
| Next.js | 15 (App Router, React Server Components) |
| React | 19 |
| Tailwind CSS | v4 (CSS-first config, no `tailwind.config.js`) |
| shadcn/ui | latest (copy-owned components) |
| TanStack Query | v5 |
| Recharts | 2.x |
| Zustand | 5 |
| next-auth | v5 |
| TypeScript | 5.5 (strict mode, no `any`) |

### CI/CD

| Tool | Version |
|---|---|
| GitHub Actions | ubuntu-latest runners |
| docker/build-push-action | v6 |
| aquasecurity/trivy-action | v0.28.0 |
| sigstore/cosign-installer | v3 |
| google-github-actions/auth | v2 (OIDC — no long-lived keys) |
| Skaffold | 2.x |
| ArgoCD | v2.12 |
| Helm | 3.x |
| Google Cloud Deploy | current |
| KEDA | 2.x |

---

## 4. API Design Principles

### 4.1 OpenAI Compatibility is Mandatory

Every inference endpoint must accept and return the OpenAI API schema. Existing
integrations using the OpenAI Python SDK, LangChain, LlamaIndex, or any
other OpenAI-compatible client must work without code changes.

### 4.2 /v1 is the Stable Surface — It is Never Broken

- Breaking changes go to `/v2` with a minimum 6-month deprecation notice
- Deprecated endpoints return a `Deprecation` header with the removal date
- Model name aliases (e.g. `gpt-4o-mini`) are version-stable

### 4.3 Every Response Includes Correlation Headers

Kong attaches to every response:

- `X-Request-ID` — a UUID identifying this request end-to-end
- `X-Platform: inference-platform`
- `X-API-Version: 1`

### 4.4 Error Responses are Structured and Actionable

```json
{
  "error": "machine_readable_code",
  "message": "Human readable description",
  "detail": { "field": "additional context" }
}
```

HTTP status codes must be semantically correct:

| Code | Meaning |
|---|---|
| 400 | Malformed request or guardrail block (pre-inference) |
| 401 | Missing or invalid authentication |
| 403 | Authenticated but not authorised |
| 404 | Resource not found |
| 413 | Request too large |
| 429 | Rate limit or budget exceeded |
| 451 | Guardrail block (post-inference, legal reason) |
| 503 | All model fallbacks exhausted |

---

## 5. Security Principles

### 5.1 Zero Plaintext Secrets in Source Control

All credentials — API keys, database passwords, JWT signing keys, Langfuse
secrets — are stored in HashiCorp Vault. `.env` files are gitignored. `.env.example`
contains only variable names with empty values.

### 5.2 Defence in Depth — Three Enforcement Layers

1. **Kong (edge)** — authentication, rate limiting, request size
2. **OPA (policy)** — authorisation, model-level ABAC
3. **Guardrails (content)** — PII, injection, toxicity

A request blocked at any layer must not proceed to the next.

### 5.3 Least Privilege Everywhere

- Each team API key is scoped to specific models and has a monthly budget ceiling
- Service-to-service calls use scoped credentials, not master keys
- The LiteLLM master key is never distributed to application teams
- OPA policies enforce team-level model restrictions beyond role-based rules

### 5.4 No Long-Lived Credentials in CI/CD

GitHub Actions authenticates to GCP via OIDC Workload Identity Federation.
No service account key files exist anywhere in the repository or CI configuration.

### 5.5 All Container Images are Signed

Every image built by CI is signed with Cosign keyless signing. Images that are
not signed by the CI pipeline are rejected at deploy time.

---

## 6. Data and Privacy Principles

### 6.1 Prompt Content is Ephemeral — See Section 2.4

This is repeated here because it is the most critical constraint in the platform.

### 6.2 PII is Redacted at the Edge, Not at the Model

Presidio runs as the first step in the guardrails pipeline. By the time a
prompt reaches LiteLLM, all detected PII has been replaced with `<REDACTED>`.
The redacted version is what appears in any downstream trace or log.

### 6.3 Audit Trail is Metadata Only

The audit trail in Loki records event metadata — timestamps, key hashes, model
names, scanner decisions — never content. If a compliance officer needs to
investigate what happened in a request, they use the Phoenix Arize trace
(which also contains no raw prompt text) to see timing and quality signals.

### 6.4 DSAR is a First-Class Platform Capability

Data subject access and deletion requests are exposed as API endpoints, not as
a manual operational process. Deletion must purge all records from PostgreSQL,
Redis, Loki, Phoenix, and Langfuse.

---

## 7. Observability Principles

### 7.1 Every Request Produces Three Correlated Signals

- A **Loki audit entry** (compliance, metadata only)
- A **Phoenix LLM span** (quality, performance, cost — no prompt content)
- A **Langfuse trace** (prompt version linkage, evaluation context)

The `X-Request-ID` value ties all three together.

### 7.2 SLOs are Defined, Measured, and Alerted

| Metric | Target | Alert threshold | Alert delay |
|---|---|---|---|
| TTFT p95 | < 400 ms | > 400 ms | 5 min |
| Error rate | < 1% | > 5% | 2 min |
| Budget utilisation per key | — | > 85% | 1 min |
| Prompt evaluation score | ≥ 0.80 | < 0.80 | immediate |

### 7.3 All Dashboards in Grafana — Six Total

1. Platform overview (TTFT, RPS, error rate, cache, cost)
2. Model health (availability, fallbacks, provider latency)
3. Guardrail activity (blocks, PII counts, scanner breakdown)
4. Cost & budget (spend vs budget, per-team, per-model)
5. Phoenix LLM traces (token usage, LLM error rate, agent depth, guardrail overlap)
6. Langfuse prompt evaluation (scores by version, cost per version, pass/fail, promotion history)

### 7.4 Memory Budget on 8 GB Mac — Stay Within Limits

| Profile combination | Limit |
|---|---|
| core only | ~620 MB |
| core + obs (with Phoenix + Langfuse) | ~2.15 GB |
| core + obs + auth | ~2.81 GB |
| All profiles | ~4.16 GB — use `docker stats` to monitor |

The platform must function with the `core` profile alone for development.
No feature may require multiple profiles running simultaneously to pass its
acceptance criteria.

---

## 8. Model and Prompt Governance Principles

### 8.1 Every Model is Registered Before it Serves Traffic

All models in the LiteLLM catalogue must have a corresponding MLflow model card
with: `provider`, `tier`, `context_window`, `training_data_cutoff`, `license`, `pricing`
(input and output per 1M tokens), `data_residency`, `gdpr_compliant`, and
`current_prompt_version`.

### 8.2 Every System Prompt is Versioned in Langfuse

No system prompt may be hardcoded in application code or configuration files.
All system prompts are registered in Langfuse with semantic versions and fetched
at runtime via `langfuse.get_prompt(name, version="production")`.

### 8.3 Promotion Requires Passing Evaluation

Model versions and prompt versions both require passing an automated evaluation
before being promoted to Production:

- **Models** — must pass pre-production evaluation harness
- **Prompts** — must score ≥ 0.80 on correctness against a golden dataset (min 20 examples)
- Rollback must be possible in a single script call for both

### 8.4 MLflow and Langfuse are Linked

The `current_prompt_version` tag on each MLflow model version must reference
the Langfuse prompt name and version number (format: `prompt-name:N`).
When a model is rolled back, the prompt version reverts with it.

---

## 9. Deployment Principles

### 9.1 Local First, Then GCP

| Environment | Stack |
|---|---|
| Development | Mac with OrbStack + Docker Compose profiles |
| Production | GCP GKE (added in Phase 10) |

The platform must be fully functional in the local Docker Compose environment.
Helm charts and Kustomize overlays adapt the same configuration for GKE.

### 9.2 GitOps — No Manual kubectl in Production

All Kubernetes deployments are driven by ArgoCD watching the Git repository.
Manual `kubectl apply` commands are prohibited in production environments.
ArgoCD self-heals manual drift within 3 minutes.

### 9.3 Canary Deployments to Production

Every production release goes through a canary at `10% → 25% → 50% → 100%`
traffic, with automatic rollback if the verification job fails. Manual rollback
via `gcloud deploy rollback` must complete within 5 minutes.

### 9.4 Container Images are Immutable and Signed

The same image SHA is deployed to dev, staging, and production — no rebuilding
per environment. Images unsigned by CI are rejected at deploy time.

---

## 10. Code Quality Standards

### 10.1 Python Services

| Rule | Requirement |
|---|---|
| Type annotations | Required on all function signatures |
| Linting | `ruff` — zero warnings |
| Type checking | `mypy` strict mode — zero errors |
| HTTP framework | FastAPI — no Flask or Django |
| HTTP client | `httpx` (async) — no `requests` |
| Instrumentation | OpenTelemetry SDK, OpenInference conventions for LLM spans |

### 10.2 TypeScript (Platform UI)

| Rule | Requirement |
|---|---|
| Type safety | TypeScript 5.5 strict mode — no `any` types |
| Type check | `tsc --noEmit` must pass with zero errors |
| API validation | All response shapes must have Zod schemas |
| Components | React 19 — Server Components by default, Client Components only when needed |
| Styling | Tailwind v4 utility classes only — no inline styles |

### 10.3 Docker Images

- All images must be multi-platform: `linux/amd64` and `linux/arm64`
- Images must pass Trivy scan with zero CRITICAL CVEs before merge
- Base images are pinned to specific tags — never use `:latest` for base images
  (exception: Phoenix Arize and Langfuse which only publish `:latest`)
- Non-root user in all custom images

### 10.4 Configuration

- No hardcoded values in code — all configuration via environment variables
- All required environment variables listed in `.env.example`
- Sensitive values sourced from Vault at runtime — not from `.env` in production

---

## 11. Acceptance Criteria Format

Every feature's done condition must be expressible as a deterministic test:
either a `curl` command with expected HTTP status and response body,
a `docker stats` snapshot showing memory within budget,
a Prometheus query returning an expected metric value,
or a Loki query returning expected log fields.

Acceptance criteria of the form "works correctly" or "looks good" are not
accepted. Every criterion must be falsifiable.

---

## 12. What This Constitution Does Not Decide

The following are deliberately left to individual feature specs:

- Specific PromQL queries for dashboard panels
- Exact Rego policy rules beyond what is stated in the RBAC table
- Langfuse dataset structure for evaluation (defined per use case)
- Grafana dashboard panel layout and colour schemes
- Helm chart resource requests/limits (defined per environment in values files)

---

> **Constitution version: 1.0** — covers Phases 00–10 including Phoenix Arize and
> Langfuse additions. Update this document via ADR before changing any invariant.

---

**Version**: 1.0.0 | **Ratified**: TODO(RATIFICATION_DATE) | **Last Amended**: 2026-05-26