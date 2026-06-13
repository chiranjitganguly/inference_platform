# Data Model: MFA TOTP Enforcement

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13

> This feature introduces **no new platform database tables or services**. All MFA state is managed natively by Keycloak within the existing `keycloak` PostgreSQL database. This document describes the Keycloak-internal schema that backs the feature and the realm configuration entities that must be authored.

---

## 1. Keycloak Native Entities (existing `keycloak` PostgreSQL DB)

### 1.1 OTP Credential (`credential` table)

Keycloak stores each user's TOTP secret as a row in its `credential` table.

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `type` | VARCHAR | `"otp"` — identifies this as a TOTP credential |
| `user_id` | UUID FK → `user_entity.id` | Owning user |
| `realm_id` | VARCHAR FK → `realm.id` | Realm scope |
| `secret_data` | TEXT (JSON) | Encrypted TOTP secret: `{"value": "<base32-secret>"}` |
| `credential_data` | TEXT (JSON) | OTP policy snapshot at enrolment: `{"subType": "totp", "period": 30, "digits": 6, "algorithm": "HmacSHA1"}` |
| `priority` | INT | Order among credentials of this type |
| `created_date` | BIGINT | Unix ms timestamp of enrolment |

**Invariants**:
- `secret_data.value` is encrypted at rest by Keycloak's credential vault (AES-GCM).
- One row per enrolled authenticator device per user (users may register multiple devices).
- Removing this row via Keycloak Admin API/console revokes MFA for that user.
- The platform never reads or writes this table directly.

### 1.2 Required Action (`required_action_provider` + `user_required_action` tables)

When an admin assigns `CONFIGURE_TOTP` to a user, a row appears in `user_required_action`.

| Column | Type | Description |
|--------|------|-------------|
| `user_id` | UUID FK | User who must complete the action |
| `required_action` | VARCHAR | `"CONFIGURE_TOTP"` |

**State transitions**:
```
[no row]  →  CONFIGURE_TOTP assigned  →  user completes enrolment  →  [row deleted, OTP credential created]
```

- Row is deleted automatically by Keycloak after the user successfully completes enrolment.
- If enrolment is abandoned mid-flow (browser closed), the row persists and the user is challenged again on their next login attempt.

### 1.3 Brute-Force Protection State (`user_entity` table)

| Column | Type | Description |
|--------|------|-------------|
| `failed_login_not_before` | BIGINT | Unix ms — user locked until this time after brute-force threshold exceeded |

Keycloak's Brute Force Detection increments a per-user failure counter in memory (session-scoped) and writes `failed_login_not_before` after the failure threshold (5 consecutive OTP failures) is crossed.

---

## 2. Realm Configuration Entities (realm-export.json)

These are declarative Keycloak config, not database tables. They are authored in `services/keycloak/realm-export.json` and auto-imported at Keycloak startup.

### 2.1 OTP Policy

```json
"otpPolicyType": "totp",
"otpPolicyAlgorithm": "HmacSHA1",
"otpPolicyInitialCounter": 0,
"otpPolicyDigits": 6,
"otpPolicyLookAheadWindow": 1,
"otpPolicyPeriod": 30,
"otpPolicyCodeReusable": false
```

`otpPolicyCodeReusable: false` enforces replay prevention (FR-007).
`otpPolicyLookAheadWindow: 1` allows ±1 time step clock drift tolerance (FR-008).

### 2.2 Brute-Force Detection Settings

```json
"bruteForceProtected": true,
"permanentLockout": false,
"maxFailureWaitSeconds": 900,
"minimumQuickLoginWaitSeconds": 60,
"waitIncrementSeconds": 60,
"quickLoginCheckMilliSeconds": 1000,
"maxDeltaTimeSeconds": 43200,
"failureFactor": 5
```

`failureFactor: 5` enforces the 5-consecutive-failures lockout (FR-009).
`permanentLockout: false` means lockout is time-limited (15 min), not permanent.

### 2.3 browser-mfa Authentication Flow

```
browser-mfa  (type: basic-flow)
├── Cookie                          ALTERNATIVE
├── browser-mfa forms               ALTERNATIVE (sub-flow)
│   ├── auth-username-password-form REQUIRED
│   └── auth-otp-form               CONDITIONAL
│       ├── Condition - User Configured  REQUIRED  (has OTP credential)
│       └── OTP Form                     REQUIRED
```

**Binding**: The `browser-mfa` flow is bound as the realm's default browser flow, replacing the standard `browser` flow.

### 2.4 Default Required Actions (realm-level)

```json
"defaultRequiredActions": ["CONFIGURE_TOTP"]
```

Setting `CONFIGURE_TOTP` as a default required action means **all new users** must enrol TOTP on first login. To make MFA optional by default and admin-assigned only, this array should be left empty; admins assign the required action per user.

> **Configuration decision**: Leave `defaultRequiredActions` empty in v1 (MFA optional per user). Admins assign `CONFIGURE_TOTP` to specific users or use group-based Keycloak policies to enforce it for specific roles.

---

## 3. Audit Log Schema (Loki — no new fields)

MFA events are captured from Keycloak container stdout by the existing Loki log pipeline. No new Loki label or schema changes are required.

**Log line structure** (Keycloak jboss-logging format):

```
type=LOGIN_ERROR realmId=inference-platform clientId=platform-ui
userId=<uuid> ipAddress=<ip> error=invalid_totp
```

**Platform audit entry** (structured fields extracted by Loki pipeline):

| Field | Source | Notes |
|-------|--------|-------|
| `event_type` | Keycloak `type` field | `LOGIN`, `LOGIN_ERROR`, `REGISTER_TOTP`, `REMOVE_TOTP` |
| `realm_id` | Keycloak `realmId` | Always `inference-platform` |
| `user_id` | Keycloak `userId` | UUID — not hashed by Keycloak; treat as pseudonymous |
| `client_id` | Keycloak `clientId` | `platform-ui` or API client |
| `error` | Keycloak `error` | `invalid_totp`, `invalid_user_credentials`, null on success |
| `timestamp` | Log line timestamp | ISO-8601 |

**No prompt content, no TOTP codes, no TOTP secrets appear in any log line** — Keycloak never logs credential values.

---

## 4. State Transition Diagram

```
User Account Created
        │
        ▼
[No OTP Credential]
        │
        ├─── Admin assigns CONFIGURE_TOTP Required Action
        │                │
        │                ▼
        │    [CONFIGURE_TOTP Required Action pending]
        │                │
        │                ├── User completes enrolment flow
        │                │              │
        │                │              ▼
        │                │    [OTP Credential active]
        │                │              │
        │                │    ┌─────────┴──────────┐
        │                │    │ Login flow          │
        │                │    │ 1. Password ✓       │
        │                │    │ 2. OTP challenge    │
        │                │    │    ├── code valid → token issued
        │                │    │    └── code invalid → rejected (FR-010)
        │                │    │         (5× → lockout, FR-009)
        │                │    └─────────────────────┘
        │                │
        │                └── User abandons enrolment → Required Action persists
        │
        └─── Admin removes OTP credential → [No OTP Credential] (recovery)
```
