# Implementation Plan: MFA TOTP Enforcement

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/025-mfa-totp-enforcement/spec.md`

---

## Summary

Enforce TOTP-based MFA for the `inference-platform` Keycloak realm by configuring a `browser-mfa` authentication flow with a CONDITIONAL OTP step. Users who are assigned MFA by an admin must complete a TOTP challenge (RFC 6238, 6-digit, 30-second window) before Keycloak issues an access token. Incorrect or missing TOTP codes block token issuance. All MFA events are captured by the existing Loki log pipeline from Keycloak container stdout.

This is a **configuration-only feature** — no new services, no new Docker images, no new database tables. Changes are confined to `services/keycloak/realm-export.json` and `scripts/smoke-test.sh`.

---

## Technical Context

**Language/Version**: JSON (Keycloak realm-export), Bash (scripts)

**Primary Dependencies**: Keycloak 24.0.3 (locked) — native OTP authenticator, brute-force detection, Required Actions

**Storage**: Keycloak `keycloak` PostgreSQL DB (existing) — `credential` table (OTP secrets), `user_required_action` table (enrolment state)

**Testing**: `curl` smoke tests against Keycloak Admin REST API + Kong proxy

**Target Platform**: Docker Compose `up-auth` profile (existing)

**Project Type**: Configuration — realm-export.json authoring only

**Performance Goals**: TOTP verification adds ≤500 ms to total auth round-trip (SC-004); handled natively by Keycloak with no additional network hops

**Constraints**: Keycloak 24.0.3 locked — no version upgrade; `up-auth` profile memory budget ~2.81 GB (unchanged — no new containers)

**Scale/Scope**: Per-user MFA assignment; no global enforcement by default

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Request Flow Integrity | ✅ PASS | MFA enforcement is upstream of Kong — it is a precondition to token issuance, not a bypass of the Kong → Guardrails → LiteLLM chain. A request that reaches Kong already carries a Keycloak-issued JWT, guaranteeing MFA completion (if required). |
| II. Prompt Content Ephemeral | ✅ PASS | MFA audit events contain only: timestamp, event_type, user_id (UUID), error code. No TOTP codes, no secrets, no prompt content appear in any log. Keycloak never logs credential values. |
| III. OpenAI API Compatibility | ✅ PASS | MFA operates at the authentication layer before any `/v1` endpoint is reached. The `/v1` API surface is unchanged. |
| IV. Defence in Depth | ✅ PASS | MFA adds a new credential factor at the Keycloak layer, preceding Kong's existing JWT validation. Strengthens the existing auth chain without bypassing any layer. |
| V. Falsifiable Acceptance Criteria | ✅ PASS | All acceptance criteria are expressed as `curl` commands with expected HTTP status and response fields. See `contracts/keycloak-flow.md` and `quickstart.md`. |

**Post-Phase-1 re-check**: All five principles continue to pass. The design introduces no new services, no new ports, and no bypasses of the existing chain.

---

## Project Structure

### Documentation (this feature)

```text
specs/025-mfa-totp-enforcement/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — 8 architectural decisions
├── data-model.md        # Phase 1 output — Keycloak schema + state transitions
├── quickstart.md        # Phase 1 output — step-by-step test guide
├── contracts/
│   ├── keycloak-flow.md     # browser-mfa flow JSON contract + acceptance tests
│   └── upstream-headers.md  # JWT claims + Kong headers (unchanged from 024)
├── checklists/
│   └── requirements.md      # Spec quality checklist (all pass)
└── tasks.md             # Phase 2 output (created by /speckit-tasks)
```

### Source Code (repository root)

```text
services/keycloak/
└── realm-export.json          # MODIFIED — add browser-mfa flow, OTP policy, brute-force config

scripts/
└── smoke-test.sh              # MODIFIED — add MFA acceptance test cases

specs/025-mfa-totp-enforcement/
└── (planning artifacts above)
```

No new files outside of `services/keycloak/` and `scripts/`. No new Docker images. No new Compose services.

**Structure Decision**: Configuration-only. All changes are in the Keycloak realm-export (declarative JSON) and the smoke test script (Bash). The realm-export is auto-imported on `keycloak` container startup, making all changes reproducible with a `make down && make up-auth`.

---

## Implementation Phases

### Phase A — Keycloak Realm Export Update

**File**: `services/keycloak/realm-export.json`

Changes required:

1. **Add `browser-mfa` flow** — copy the existing `browser` flow structure and add the CONDITIONAL OTP sub-flow (see `contracts/keycloak-flow.md` for exact JSON).

2. **Bind `browser-mfa` as the realm browser flow**:
   ```json
   "browserFlow": "browser-mfa"
   ```

3. **Set OTP policy**:
   ```json
   "otpPolicyType": "totp",
   "otpPolicyAlgorithm": "HmacSHA1",
   "otpPolicyDigits": 6,
   "otpPolicyPeriod": 30,
   "otpPolicyLookAheadWindow": 1,
   "otpPolicyCodeReusable": false
   ```

4. **Enable brute-force protection**:
   ```json
   "bruteForceProtected": true,
   "permanentLockout": false,
   "failureFactor": 5,
   "maxFailureWaitSeconds": 900
   ```

5. **Leave `defaultRequiredActions` empty** (MFA optional, admin-assigned per user).

**Validation**: After `make down && make up-auth`, run the acceptance tests in `contracts/keycloak-flow.md`.

### Phase B — Smoke Test Extension

**File**: `scripts/smoke-test.sh`

Add MFA-specific checks:
- Verify `browserFlow` == `"browser-mfa"` via Keycloak Admin API.
- Verify OTP policy fields match the contract values.
- Verify `bruteForceProtected` == `true` and `failureFactor` == `5`.
- Verify Kong still returns `401` for requests without a valid JWT (regression check).

### Phase C — Documentation Update

**File**: `docs/progress.md`

Mark feature 025 as complete with acceptance test results.

---

## Complexity Tracking

No constitution violations. No complexity tracking entry required.

---

## Key Risk

**Realm export merge conflict**: The `realm-export.json` from feature 024 contains a full realm snapshot. Adding the `browser-mfa` flow must be done as a targeted JSON edit, not a full replacement, to avoid clobbering Keycloak client definitions, user mappings, and Kong JWT consumer configuration from feature 024. Verify the merged export with `jq` before committing.
