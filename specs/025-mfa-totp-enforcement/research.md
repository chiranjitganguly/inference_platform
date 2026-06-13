# Research: MFA TOTP Enforcement

**Branch**: `025-mfa-totp-enforcement` | **Date**: 2026-06-13

---

## Decision 1 — MFA Implementation Strategy: Keycloak Native vs. Custom Service

**Decision**: Use Keycloak's built-in OTP authenticator (RFC 6238 TOTP) exclusively. No custom MFA service or external TOTP library.

**Rationale**: Keycloak 24.0.3 ships a production-grade, battle-tested TOTP implementation — `OTPFormAuthenticator` + `OTPPolicy` — that handles secret generation, QR code provisioning, time-window validation, replay prevention, and brute-force protection natively. Building a parallel implementation would duplicate this logic, introduce a new attack surface, and require maintaining its own credential store outside Keycloak.

**Alternatives considered**:
- Custom FastAPI TOTP service (pyotp + PostgreSQL): rejected — unnecessary complexity, duplicates Keycloak capability, adds latency hop.
- External MFA provider (Duo, Okta Verify): rejected — out of scope, adds third-party dependency, incompatible with self-hosted air-gap requirements.

---

## Decision 2 — Keycloak Authentication Flow: browser-mfa

**Decision**: Create a new `browser-mfa` authentication flow in the `inference-platform` realm with these execution steps:

| Step | Authenticator | Requirement | Notes |
|------|--------------|-------------|-------|
| 1 | Cookie | ALTERNATIVE | Skip if valid session cookie |
| 2 | Kerberos | DISABLED | Not used |
| 3 | Identity Provider Redirector | ALTERNATIVE | SSO redirect if configured |
| 4 | browser-mfa forms | ALTERNATIVE | Sub-flow containing steps 5–6 |
| 5 | auth-username-password-form | REQUIRED | Password must succeed first |
| 6 | auth-otp-form | CONDITIONAL | Runs if user has OTP configured OR is required by policy |

The CONDITIONAL requirement on step 6 allows per-user and per-group policy enforcement without blocking users who have not been assigned MFA.

**Rationale**: Keycloak's built-in `browser` flow does not include an OTP step. Copying and extending it into `browser-mfa` follows Keycloak best practice — never modify the built-in flows, always copy and extend. The CONDITIONAL requirement is what enables "optional per user but enforceable via policy": Keycloak's `ConditionalOtpFormAuthenticator` checks whether OTP is required for the user (via Required Action, group attribute, or role) before challenging them.

**Alternatives considered**:
- REQUIRED OTP for all users: rejected — user confirmed MFA is optional per user.
- ALTERNATIVE OTP (user-initiated): rejected — provides no enforcement guarantee.

---

## Decision 3 — MFA Enrolment UX: Keycloak Account Console + Required Action

**Decision**: Users enrol their TOTP device via the Keycloak account console (`http://localhost:8083/realms/inference-platform/account`). When a Keycloak policy requires MFA for a user who has not enrolled, the `CONFIGURE_TOTP` Required Action fires inline during login, redirecting the user to the enrolment QR page within the login flow before issuing a token.

**Rationale**: Keycloak's account console provides a fully functional, accessible TOTP enrolment UI including QR code display, manual secret entry fallback, OTP policy information (digits, period, algorithm), and enrolment confirmation. Building a parallel enrolment UI in platform-ui adds maintenance cost with no user-experience gain. The Required Action mechanism ensures zero-click admin setup for enforcement: assign `CONFIGURE_TOTP` as a required action on the user or via the realm default required actions list.

**Alternatives considered**:
- Custom platform-ui enrolment page: rejected — user confirmed Keycloak account console is sufficient for v1.
- Bulk pre-provisioning via TOTP secret import: rejected — breaks the self-service enrolment model and requires distributing raw secrets.

---

## Decision 4 — Enforcement Granularity: OTP Policy + Conditional Flow

**Decision**: Three complementary mechanisms enforce MFA at different granularities:

1. **Realm-wide default**: Set `otpPolicyType: totp`, `otpPolicyPeriod: 30`, `otpPolicyDigits: 6`, `otpPolicyAlgorithm: HmacSHA1` in realm OTP policy.
2. **Per-user enforcement**: Assign the `CONFIGURE_TOTP` required action to individual users in the Keycloak admin console.
3. **Policy-driven**: Enable the `Condition - User Configured` sub-condition in the browser-mfa flow's CONDITIONAL step to require OTP for users who have it configured, and optionally add a `Condition - User Role` condition to force OTP for specific roles (e.g., `admin`, `operator`).

**Rationale**: This layered approach satisfies "optional per user but enforceable via Keycloak policy" without code changes — admins toggle enforcement through the Keycloak UI.

**Alternatives considered**:
- Group-attribute-based conditions: valid but adds complexity; role-based conditions cover the same use cases for this platform.

---

## Decision 5 — Backup Codes: Out of Scope

**Decision**: Backup codes are not implemented in v1. Account recovery for a lost authenticator device is handled by an admin removing the user's OTP credential via the Keycloak admin console or Keycloak Admin REST API.

**Rationale**: Keycloak's built-in OTP implementation does not provide native backup codes. Implementing them would require a custom Keycloak SPI extension — a significant scope addition. The admin-reset recovery path is operationally sufficient for the platform's user base in v1.

**Alternatives considered**:
- Custom backup-code SPI in Keycloak: deferred to v2 if recovery demand warrants it.
- Storing backup codes in a separate PostgreSQL table: rejected — adds out-of-Keycloak state that must be kept in sync.

---

## Decision 6 — Audit Logging: Keycloak Event Listener → Loki

**Decision**: Enable Keycloak's built-in `jboss-logging` event listener for MFA events. Forward Keycloak container logs to the existing Loki stack via Docker log driver. No custom event listener SPI required.

**Keycloak events to capture** (type codes):

| Keycloak Event Type | MFA Meaning |
|---------------------|-------------|
| `LOGIN` | Successful password + OTP verification |
| `LOGIN_ERROR` | Failed verification (includes `error=invalid_user_credentials` or `error=invalid_totp`) |
| `REGISTER_TOTP` | Successful OTP enrolment |
| `REMOVE_TOTP` | OTP credential removed (admin reset) |

Loki query to find MFA failures:
```logql
{container="keycloak"} |= "LOGIN_ERROR" |= "invalid_totp"
```

**Rationale**: Keycloak already emits structured events for all auth operations. The existing Loki pipeline (configured in feature 024) captures Keycloak container logs. No new audit infrastructure is needed. Aligns with Constitution Principle II — event metadata only, no credentials or codes in logs.

**Alternatives considered**:
- Custom Keycloak Event Listener SPI writing directly to Loki HTTP endpoint: valid but adds a custom JAR to the Keycloak image, complicating upgrades. Deferred to v2 if structured Loki queries require event normalization.

---

## Decision 7 — Kong Integration: No Changes Required

**Decision**: The Kong JWT plugin (configured in feature 024) requires no modification. Keycloak issues JWTs only after the full browser-mfa flow completes (password + OTP). Kong validates the JWT signature and expiry as before — the MFA enforcement happens upstream at Keycloak, before token issuance.

**Rationale**: Kong's role is to validate *that a valid token exists*, not *how it was obtained*. Adding MFA to the Keycloak flow is transparent to Kong. No Kong plugin changes, route changes, or seed script changes are needed.

---

## Decision 8 — OTP Policy Parameters

**Decision**: Use Keycloak defaults for RFC 6238 compliance:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Algorithm | HmacSHA1 | RFC 6238 default; widest authenticator-app compatibility |
| Digits | 6 | Standard; supported by all RFC 6238 apps |
| Period | 30 seconds | RFC 6238 standard window |
| Clock drift tolerance | ±1 window (±30 s) | Keycloak default `lookAheadWindow=1` |
| Brute-force protection | 5 consecutive failures → lockout | Keycloak Brute Force Detection setting |

**Rationale**: RFC 6238 default parameters maximise compatibility with all TOTP authenticator apps. Non-default parameters (e.g., 8-digit codes, 60-second windows) would break compatibility with apps that only support standard configurations.
