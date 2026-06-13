# Feature Specification: MFA TOTP Enforcement

**Feature Branch**: `025-mfa-totp-enforcement`

**Created**: 2026-06-13

**Status**: Draft

**Input**: User description: "Build MFA enforcement so users must complete a second authentication factor after submitting their password before receiving an access token. TOTP-based MFA must be supported. An incorrect TOTP code must prevent token issuance."

## Clarifications

### Session 2026-06-13

- Q: Where is MFA configured and stored? → A: MFA is configured per user in Keycloak; Keycloak's native OTP credential store is the authority for enrolment state.
- Q: Which TOTP standard and which apps are supported? → A: TOTP per RFC 6238; any RFC 6238-compliant authenticator app is supported (not restricted to specific apps).
- Q: Does an incorrect TOTP code block token issuance even when the password was correct? → A: Yes — an incorrect TOTP code always prevents token issuance regardless of password validity.
- Q: Is MFA globally mandatory or selectively enforced? → A: MFA is optional per user but enforceable via Keycloak authentication policy (realm-level or per-group/user assignment).
- Q: When policy requires MFA for an unenrolled user, how does the system respond? → A: User is prompted to enrol TOTP inline during the current login flow via Keycloak Required Action (Configure OTP) before the session completes.
- Q: What Keycloak authentication flow is used? → A: browser-mfa flow — auth-username-password-form step REQUIRED, followed by auth-otp-form step CONDITIONAL.
- Q: Where do users enrol their TOTP device? → A: Via Keycloak account console; no separate platform-ui MFA management page in v1.
- Q: Are backup codes in scope? → A: No — using Keycloak's built-in RFC 6238 OTP support only; backup codes are not a native Keycloak capability; recovery is via Keycloak admin console.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-Time MFA Enrolment (Priority: P1)

A user logs in for the first time after MFA is mandated. After entering their password they are prompted to enrol a TOTP authenticator app. They scan a QR code, enter the six-digit code from their app to confirm setup, and only then receive an access token.

**Why this priority**: No user can complete the login flow without this path working. It is the entry point to every subsequent MFA interaction.

**Independent Test**: Can be fully tested by creating a fresh user account with no MFA configured, attempting login, and verifying that an access token is not issued until enrolment is confirmed with a valid TOTP code.

**Acceptance Scenarios**:

1. **Given** a user with no MFA configured, **When** they submit valid credentials, **Then** the system presents an MFA enrolment prompt rather than issuing an access token.
2. **Given** the enrolment prompt is displayed, **When** the user scans the QR code and enters the correct six-digit TOTP code, **Then** MFA is recorded as enrolled and an access token is issued.
3. **Given** the enrolment prompt is displayed, **When** the user enters an incorrect TOTP code, **Then** the system rejects the code and no access token is issued.

---

### User Story 2 - Returning User MFA Challenge (Priority: P1)

A user who has already enrolled an authenticator app logs in by entering their password. The system then requires them to enter a current TOTP code before issuing any access token.

**Why this priority**: This is the steady-state authentication path for every user on every login after enrolment is complete.

**Independent Test**: Can be fully tested by enrolling MFA on a test account, logging in with correct credentials, and verifying that the access token is only issued after a valid TOTP code is submitted.

**Acceptance Scenarios**:

1. **Given** an MFA-enrolled user, **When** they submit correct credentials, **Then** the system prompts for a TOTP code before issuing an access token.
2. **Given** the TOTP challenge is presented, **When** the user submits the current valid code, **Then** the system issues an access token.
3. **Given** the TOTP challenge is presented, **When** the user submits an expired or incorrect code, **Then** the system rejects it and no access token is issued.
4. **Given** the TOTP challenge is presented, **When** the user submits an empty code, **Then** the system rejects the submission.

---

### User Story 3 - MFA Bypass Attempt Blocked (Priority: P1)

An attacker or automated client that has obtained valid credentials attempts to receive an access token without completing the MFA step.

**Why this priority**: Enforcing MFA at the token-issuance level is the security guarantee this feature must provide.

**Independent Test**: Can be fully tested by sending a direct token request with valid credentials but no TOTP code and verifying the response is a denial with no token.

**Acceptance Scenarios**:

1. **Given** a client with valid credentials, **When** it requests an access token without supplying a TOTP code, **Then** the system denies the request and returns no token.
2. **Given** a client supplies a TOTP code that does not match the enrolled secret, **When** a token is requested, **Then** the system denies the request and returns no token.

---

### Edge Cases

- What happens when the TOTP code is entered at the exact boundary of a 30-second window? The system must accept a code that was valid within a one-step clock-drift tolerance (±30 seconds).
- What happens if a user submits the TOTP code more than once? The system must not allow replay; a code used once must be invalidated for the duration of its time window.
- What happens when a user fails TOTP verification repeatedly? After five consecutive failures the session must be invalidated and the user must re-enter their password.
- What happens if the user's authenticator clock is skewed? The system must accept codes from one adjacent time step (30 s) either side to tolerate moderate clock drift.
- What happens if MFA enrolment is abandoned mid-flow? No access token is issued; the partial enrolment is discarded.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST NOT issue an access token until both the user's password and a valid TOTP code have been verified within the same authentication session.
- **FR-002**: The system MUST support TOTP as defined by RFC 6238, using a 30-second time step and six-digit codes.
- **FR-003**: When a Keycloak authentication policy requires MFA for a user with no TOTP enrolled, the system MUST prompt that user to complete TOTP enrolment inline during the login flow before issuing an access token (Keycloak Required Action: Configure OTP).
- **FR-004**: The system MUST present a scannable QR code during enrolment so the user can register their authenticator app without manually entering a secret key.
- **FR-007**: The system MUST reject any TOTP code that has already been used within its current 30-second validity window (replay prevention).
- **FR-008**: The system MUST accept TOTP codes from one adjacent time step (±30 seconds) to tolerate minor clock drift.
- **FR-009**: After five consecutive failed TOTP attempts in a single session, the system MUST invalidate the session and require the user to re-authenticate from the password step.
- **FR-010**: An incorrect TOTP code MUST result in an explicit rejection response with no access token included.
- **FR-011**: All MFA events (enrolment, successful verification, failed verification, lockout) MUST be recorded in the audit log with timestamp, user identity, and event type — no TOTP secrets or codes are logged.

### Key Entities

- **MFA Enrolment**: Keycloak-native OTP credential attached to a user account; Keycloak is the authority for enrolment state. The platform does not maintain a separate copy of the TOTP secret.
- **TOTP Verification Attempt**: Transient within a single Keycloak session; used for replay prevention and consecutive-failure counting; not persisted after the session ends.
- **MFA Audit Event**: An immutable log entry in Loki capturing event type, user identity reference (hashed), timestamp, and outcome — never containing raw codes or secrets.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every login attempt by an enrolled user issues an access token only after a valid TOTP code is submitted — 0% of tokens are issued without MFA verification.
- **SC-002**: Every login attempt without a TOTP code (or with an incorrect code) is rejected — 0% bypass rate.
- **SC-003**: Users can complete the first-time MFA enrolment flow in under two minutes under normal network conditions.
- **SC-004**: TOTP verification adds no more than 500 milliseconds to the total authentication round-trip time as perceived by the user.
- **SC-005**: 95% of users successfully complete the TOTP challenge on their first attempt in production (measured over a 30-day window after rollout).
- **SC-006**: All MFA events appear in the audit log within five seconds of occurrence.
- **SC-007**: After five consecutive TOTP failures the session is locked out 100% of the time with no token issued.

## Assumptions

- Users have access to any RFC 6238-compliant TOTP authenticator app; no specific app is required or preferred.
- MFA is optional per user and is not globally mandatory by default. Administrators enforce it by configuring a Keycloak authentication policy at realm, group, or individual-user level.
- The existing SSO/Keycloak session layer from feature 024 provides the authenticated-but-not-yet-token-issued state that MFA enforcement intercepts.
- Backup codes are out of scope; Keycloak's built-in OTP support does not provide native backup codes. Account recovery for a user who loses their authenticator device is handled by an admin removing the OTP credential via the Keycloak admin console.
- There is no self-service MFA management page in platform-ui v1; users manage their enrolled authenticators via the Keycloak account console.
- SMS or email-based OTP is out of scope; only TOTP authenticator apps are supported in v1.
- Prompt content is never included in audit log entries, consistent with the platform constitution.
