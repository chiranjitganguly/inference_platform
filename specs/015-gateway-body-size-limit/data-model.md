# Data Model: Gateway Request Body Size Limit

**Feature**: 015-gateway-body-size-limit | **Date**: 2026-06-02

---

## Overview

This feature introduces no new persistent data entities. All state is plugin configuration stored in Kong's existing PostgreSQL database (DB mode) or in-memory config (DB-less mode). The relevant logical entities are:

---

## Entity: SizeLimitPolicy

Represents the global request size enforcement rule applied at the Kong edge.

| Attribute | Type | Value | Notes |
|---|---|---|---|
| `plugin_name` | string | `request-size-limiting` | Built-in Kong plugin identifier |
| `scope` | enum | `global` | No service/route association — applies to all traffic |
| `allowed_payload_size` | integer (MB) | `10` | Externally configurable; unit is whole megabytes |
| `size_unit` | enum | `megabytes` | Kong 3.6 default unit for this plugin |

**Lifecycle**: Created during `make seed-kong`. Updated by re-running the seed or patching via Kong Admin API (`PATCH /plugins/{id}`). No migration needed — Kong persists plugin config in the `plugins` table.

**State transitions**: None — the policy is either present (enforced) or absent (unenforced). There is no draft/staged state.

---

## Entity: RejectionAuditEntry (Loki log record)

Every 413 rejection produces a Kong access log entry with the following metadata fields. This is not a new table — it is a structured log event flowing into Loki via the existing pipeline.

| Field | Source | Example |
|---|---|---|
| `timestamp` | Kong access log | `2026-06-02T14:23:01.000Z` |
| `status` | HTTP response code | `413` |
| `request_id` | `X-Request-ID` header | `a3f7c291-...` |
| `route` | Kong route name | `litellm-proxy` |
| `method` | HTTP method | `POST` |
| `path` | Request path | `/v1/chat/completions` |
| `consumer` | Kong consumer username | `smoke-test-consumer` |
| `request_length` | Declared or measured bytes | `12582912` |
| `upstream_uri` | — | *(absent — not forwarded)* |

**Constraint**: No prompt content, model name, or response text appears in this record. Compliant with Constitution Principle II.

---

## No Schema Migrations Required

Kong manages its own schema. The `request-size-limiting` plugin table exists in Kong 3.6 by default. Running `seed-kong.sh` registers a row in the `plugins` table via the Admin API — no `init-db.sql` changes needed.
