# Feature Specification: Enterprise SSO JWT Validation

**Feature Branch**: `024-enterprise-sso-jwt`

**Created**: 2026-06-09

**Status**: Clarified

**Input**: Build enterprise SSO so users authenticate via an OIDC identity provider and receive a JWT validated at the gateway before any request reaches internal services. The JWT must contain the user's roles and team as claims. The gateway must validate the JWT signature, reject expired tokens, and reject tampered tokens.

---

## Clarifications

### Session 2026-06-09

- Q: How is the JWT signature validated — generic JWKS or Keycloak-specific? → A: Validated against the Keycloak realm's RS256 public key, fetched from the realm's JWKS endpoint.
- Q: What are the `roles` claim values? → A: An array of Keycloak realm role names (not client roles).
- Q: Where does the `team` claim value originate in Keycloak? → A: Populated from a Keycloak user attribute via a protocol mapper.
- Q: Do Phoenix and Langfuse UIs require authentication? → A: Yes — each requires separate Keycloak-issued tokens configured per service.
- Q1: Does this feature include configuring JWT-protected access to Phoenix and Langfuse UIs? → A: Yes, in scope — add requirements for both UI token configurations to this feature.
- Q2: Should the gateway apply a clock-skew grace period for `exp` and `nbf` validation? → A: Fixed 30-second grace — accept tokens expired or not-yet-valid by up to 30 seconds.
- Q3: Should `roles` and `team` claim values be written to the audit log? → A: Yes — both are organisational metadata, not prompt content; log as named fields in every audit entry.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Developer Authenticates and Calls the API (Priority: P1)

A developer logs in through their organisation's identity provider (IdP). After a successful login they receive a short-lived JWT. They include that token in every subsequent API request via the `Authorization: Bearer` header. The gateway validates the token silently and forwards the request to the LLM service. From the developer's perspective the API works exactly as before — only the authentication mechanism changes.

**Why this priority**: Core gate-keeping behaviour. Nothing else in this feature is testable until requests can be authenticated end-to-end.

**Independent Test**: A curl request with a valid, non-expired JWT issued by the configured IdP returns `200 OK` with an LLM response. No other user story needs to be implemented first.

**Acceptance Scenarios**:

1. **Given** a user has a valid JWT signed by the configured IdP, **When** they send a `POST /v1/chat/completions` request with `Authorization: Bearer <token>`, **Then** the gateway forwards the request and returns the LLM response with `200 OK`.
2. **Given** a request arrives with no `Authorization` header, **When** the gateway evaluates it, **Then** it returns `401 Unauthorized` and the request never reaches internal services.
3. **Given** a request arrives with a malformed or structurally invalid token, **When** the gateway evaluates it, **Then** it returns `401 Unauthorized`.

---

### User Story 2 — Gateway Rejects Expired and Tampered Tokens (Priority: P1)

A token issued yesterday is no longer valid; a token whose payload has been edited client-side is fraudulent. Both must be blocked at the gateway before touching any internal service.

**Why this priority**: Security invariant — equally critical to P1 acceptance flow. Both scenarios must block traffic unconditionally.

**Independent Test**: Sending a known-expired token returns `401 Unauthorized`. Sending a token with a modified payload (but otherwise intact header/signature) returns `401 Unauthorized`. Both verifiable with a single curl per case.

**Acceptance Scenarios**:

1. **Given** a JWT whose `exp` claim is in the past, **When** it is presented to the gateway, **Then** the gateway returns `401 Unauthorized` and no request reaches LiteLLM or Guardrails.
2. **Given** a JWT whose payload has been base64-edited to change a claim value (signature now invalid), **When** it is presented to the gateway, **Then** the gateway returns `401 Unauthorized`.
3. **Given** a JWT signed with an unknown or revoked key, **When** it is presented to the gateway, **Then** the gateway returns `401 Unauthorized`.

---

### User Story 3 — Role and Team Claims Drive Downstream Authorisation (Priority: P2)

The JWT carries `roles` and `team` claims. Downstream services (OPA, Guardrails) use these claims to enforce access control — for example, preventing a `viewer` role from accessing expensive models, or restricting a team to a subset of the model catalogue. The gateway must extract and forward these claims so downstream services do not need to re-validate the token.

**Why this priority**: Enables policy enforcement; dependent on P1 being functional but decoupled from token validation itself.

**Independent Test**: A request bearing a JWT with `roles: ["viewer"]` and `team: "data-science"` reaches OPA; OPA can read both values from the forwarded request context. Verifiable by inspecting OPA decision logs.

**Acceptance Scenarios**:

1. **Given** a valid JWT containing `roles: ["admin"]` and `team: "platform"`, **When** the gateway forwards the request, **Then** the downstream service receives both claims in a well-known header or context field.
2. **Given** a valid JWT that is missing the `roles` claim, **When** the gateway evaluates it, **Then** it returns `403 Forbidden` (token is authentic but non-conformant).
3. **Given** a valid JWT that is missing the `team` claim, **When** the gateway evaluates it, **Then** it returns `403 Forbidden`.

---

### User Story 4 — Token Refresh Without User Disruption (Priority: P3)

Access tokens are short-lived (≤1 hour). When a developer's client exchanges a refresh token for a new access token, the new token is accepted immediately by the gateway without any manual re-configuration.

**Why this priority**: Quality-of-life for long-running sessions; does not affect core security posture.

**Independent Test**: A refresh-token exchange produces a new JWT; a subsequent API call with the new JWT returns `200 OK` without any gateway restart or cache flush.

**Acceptance Scenarios**:

1. **Given** a valid access token has expired, **When** the client exchanges its refresh token with the IdP and re-sends the request with the new access token, **Then** the gateway accepts the new token and returns `200 OK`.
2. **Given** the IdP rotates its signing keys, **When** the gateway next attempts JWKS resolution, **Then** it fetches the updated key set and validates subsequent tokens correctly within 5 minutes.

---

### User Story 5 — Platform Admin Accesses Phoenix and Langfuse UIs (Priority: P2)

A platform administrator needs to inspect LLM traces in Phoenix Arize and manage prompt versions in Langfuse. Both UIs must require a valid Keycloak-issued token before serving any page. Each service is configured independently to trust the same Keycloak realm. Users without a valid token are redirected to the Keycloak login page.

**Why this priority**: These UIs expose sensitive LLM trace data and all prompt versions. Leaving them unprotected while the API is gated would create a direct security gap — anyone with network access could read production traces without authenticating.

**Independent Test**: Accessing Phoenix UI at port 6006 without a token redirects to Keycloak login. After authenticating, the UI loads. Same behaviour for Langfuse UI at port 3002.

**Acceptance Scenarios**:

1. **Given** an unauthenticated browser request to the Phoenix UI, **When** the request arrives at port 6006, **Then** the user is redirected to the Keycloak login page.
2. **Given** an unauthenticated browser request to the Langfuse UI, **When** the request arrives at port 3002, **Then** the user is redirected to the Keycloak login page.
3. **Given** an authenticated user with a valid Keycloak-issued token, **When** they access either UI, **Then** the UI loads and displays their authorised content.
4. **Given** a valid token issued for the API audience (not the UI audience), **When** it is presented to the Phoenix or Langfuse UI, **Then** access is denied (wrong audience).

---

### Edge Cases

- What happens when the IdP's JWKS endpoint is temporarily unreachable? Gateway must fail closed (reject requests) and return `503 Service Unavailable` rather than bypassing validation.
- What happens when a JWT contains multiple `aud` (audience) values? Gateway must accept the token if the configured audience is among the listed values.
- What happens when a token is presented before its `nbf` (not-before) claim? If within the 30-second grace window the gateway accepts it; if more than 30 seconds early it rejects with `401 Unauthorized`.
- What happens when `roles` is an empty array `[]`? Gateway accepts the token structure but downstream OPA policy determines access; the gateway itself does not block.
- What happens if the JWKS cache TTL has not expired but a newly issued token uses a key ID (`kid`) not yet in the cache? Gateway must attempt a JWKS refresh before rejecting.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST require a valid JWT in the `Authorization: Bearer` header on all `/v1/*` routes.
- **FR-002**: The gateway MUST validate the JWT signature against the Keycloak realm's RS256 public key, fetched from the realm's JWKS endpoint (`{keycloak-host}/realms/{realm}/protocol/openid-connect/certs`).
- **FR-003**: The gateway MUST reject any token whose `exp` claim is more than 30 seconds in the past, returning `401 Unauthorized`. A fixed 30-second clock-skew grace period is applied to tolerate clock drift between Keycloak and the gateway.
- **FR-004**: The gateway MUST reject any token whose signature does not match the declared signing key, returning `401 Unauthorized`.
- **FR-005**: The gateway MUST reject any token whose `nbf` claim is more than 30 seconds in the future, returning `401 Unauthorized`. The same 30-second clock-skew grace period applies.
- **FR-006**: The gateway MUST reject tokens that lack a `roles` claim or a `team` claim, returning `403 Forbidden`.
- **FR-007**: The gateway MUST forward the `roles` and `team` claim values to downstream services in a consistent, documented format.
- **FR-008**: The gateway MUST cache JWKS keys to avoid a round-trip to the IdP on every request; the cache MUST be refreshed at least every 5 minutes.
- **FR-009**: When the JWKS endpoint is unreachable and the cache is stale, the gateway MUST fail closed and return `503 Service Unavailable`.
- **FR-010**: The gateway MUST validate the JWT `aud` claim against the configured platform audience; tokens with a non-matching audience MUST be rejected with `401 Unauthorized`.
- **FR-011**: The gateway MUST validate the JWT `iss` claim against the configured IdP issuer URL; tokens from unknown issuers MUST be rejected with `401 Unauthorized`.
- **FR-012**: All authentication decisions MUST be recorded in the audit log with the following fields: `timestamp`, `request_id`, `event_type` (auth_accepted / auth_rejected), `reason_code`, `sub` (token subject), `roles` (array), `team` (string), `model` (if present in request). Prompt content and raw token strings MUST NOT be persisted. The `roles` and `team` values are classified as organisational metadata, not prompt content, and are explicitly permitted in audit entries.
- **FR-013**: The IdP issuer URL, JWKS URI, expected audience, and required claims MUST be configurable without a gateway restart.
- **FR-014**: Health and liveness checks for the gateway MUST be exempt from JWT validation.
- **FR-015**: Phoenix UI (port 6006) MUST require a valid Keycloak-issued token; unauthenticated requests MUST be redirected to the Keycloak login page. The token audience for Phoenix MUST be distinct from the API audience.
- **FR-016**: Langfuse UI (port 3002) MUST require a valid Keycloak-issued token; unauthenticated requests MUST be redirected to the Keycloak login page. The token audience for Langfuse MUST be distinct from the API audience.
- **FR-017**: Each UI service (Phoenix, Langfuse) MUST be independently configured with its own Keycloak client and audience value; a token valid for one service MUST NOT grant access to the other.

### Key Entities

- **Identity Provider (IdP)**: The authoritative source of user identity; issues signed JWTs and publishes JWKS. For this platform the existing Keycloak instance (port 8083) serves this role.
- **JWT (JSON Web Token)**: A signed, short-lived bearer credential containing at minimum: `sub`, `iss`, `aud`, `exp`, `iat`, `nbf`, `roles` (array of Keycloak realm role name strings), `team` (string sourced from a Keycloak user attribute).
- **JWKS (JSON Web Key Set)**: The realm's RS256 public key material published by Keycloak at `{keycloak-host}/realms/{realm}/protocol/openid-connect/certs`, used by the gateway to verify JWT signatures without contacting Keycloak per-request.
- **Consumer**: An authenticated principal (user or service account) identified by the JWT `sub` claim. Used downstream for rate-limiting and spend tracking.
- **Realm Role**: A Keycloak realm-level role name (not a client role) carried in the `roles` array claim. Examples: `admin`, `engineer`, `viewer`. The gateway enforces that at least one role is present; OPA enforces which roles are authorised for which operations.
- **Team**: A string claim populated from a Keycloak user attribute via a protocol mapper, associating the consumer with an organisational unit used for quota and model-access segmentation.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every request that reaches LiteLLM or Guardrails carries a verified identity — zero unauthenticated requests pass through the gateway under normal operation.
- **SC-002**: Token validation adds no more than 5 ms of latency at the 99th percentile under normal load (JWKS cache warm).
- **SC-003**: Expired, tampered, or unsigned tokens are rejected 100% of the time with no false negatives.
- **SC-004**: The gateway recovers and begins accepting valid tokens within 5 minutes of an IdP signing-key rotation, without a restart.
- **SC-005**: All authentication rejections are recorded in the audit log within 1 second of the decision, with no prompt content persisted.
- **SC-006**: A development team can configure a new IdP (issuer URL, JWKS URI, audience) and have it active within 10 minutes, with no downtime.
- **SC-007**: The smoke test suite (`make smoke`) passes end-to-end with JWT authentication enabled, covering at least: valid token accepted, expired token rejected, tampered token rejected, missing token rejected.
- **SC-008**: Phoenix UI and Langfuse UI each redirect unauthenticated browser requests to the Keycloak login page; a token valid for one service does not grant access to the other.
- **SC-009**: Every audit log entry for an authentication event contains `roles`, `team`, `sub`, `request_id`, and `reason_code`; no raw token string or prompt content appears in any log entry.

---

## Assumptions

- The existing Keycloak instance (port 8083) serves as the OIDC identity provider; no new IdP needs to be provisioned.
- Keycloak is pre-configured to issue tokens with `roles` (via a realm role mapper) and `team` (via a user attribute protocol mapper) claims; Keycloak mapper configuration is out of scope for this feature but must exist before acceptance testing.
- Only the `/v1/*` route namespace requires JWT authentication; the Kong admin API (port 8001) and internal service-to-service calls remain on existing auth mechanisms.
- Health-check endpoints (`/health`, `/healthz`, `/ping`) are explicitly excluded from JWT validation.
- Token lifetime policy (access token TTL, refresh token TTL) is managed by Keycloak administrators and is out of scope.
- The `roles` claim is a JSON array of strings; the `team` claim is a single string.
- The platform runs a single Keycloak realm for all consumers; multi-realm support is out of scope for this feature.
- Phoenix UI (port 6006) and Langfuse UI (port 3002) each require their own Keycloak client and audience configuration; both are in scope for this feature (FR-015–FR-017).
- Service-to-service calls within the Docker Compose network do not pass through Kong and are therefore out of scope.
- Client SDKs and developer tooling for obtaining tokens are out of scope; documentation pointing to the Keycloak token endpoint is sufficient.
