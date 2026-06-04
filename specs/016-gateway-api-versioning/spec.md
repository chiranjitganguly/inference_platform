# Feature Specification: Gateway API Versioning

**Feature Branch**: `016-gateway-api-versioning`

**Created**: 2026-06-03

**Status**: Clarified

**Input**: User description: "Build API versioning at the gateway so a stable v1 surface exists for current integrations and a v2 path is available for future breaking changes. Every response must carry a header identifying which version was used. Deprecated endpoints must signal their deprecation with a removal date at least 6 months in advance."

## Clarifications

### Session 2026-06-03

- Q: What is the role of v1 relative to existing integrations? → A: v1 is the stable backward-compatible surface — existing integrations are never broken by changes made to v2.
- Q: Does v2 route to a different backend upstream than v1? → A: No — v2 routes to the same LiteLLM upstream as v1; the version prefix controls the routing tier and header, not the backend target.
- Q: What deprecation signal should deprecated endpoints emit? → A: The `Deprecation` header (RFC 8594 announcement date) accompanied by `Sunset` (removal date) and `Link` (migration target) — user confirmed "Deprecation header with a date" aligns with this standard set.
- Q: Are model name aliases version-stable across v1 and v2? → A: Yes — aliases like `gpt-4o-mini`, `claude-sonnet`, etc. resolve to the same model regardless of version prefix; no client remapping is required when moving from v1 to v2.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Existing integrations routed to v1 without changes (Priority: P1)

An API consumer that currently calls the platform (e.g., `POST /chat/completions`) continues to work without any modification to their client code. Their existing requests are automatically handled under the v1 surface, and every response they receive now includes a header confirming they are on version 1.

**Why this priority**: Backward compatibility is the contract at the heart of this feature. Breaking existing integrations would be a regression, not a versioning scheme.

**Independent Test**: Issue the existing unversioned or v1-prefixed request and confirm a 200 response is returned along with the version header — no client changes required.

**Acceptance Scenarios**:

1. **Given** a client sends `POST /v1/chat/completions` with a valid payload, **When** the gateway processes the request, **Then** the response status is 200 and the response carries a header `X-API-Version: v1`.
2. **Given** a client sends a request without an explicit version prefix (legacy path), **When** the gateway processes the request, **Then** it is treated as v1, returns the same result, and includes `X-API-Version: v1` in the response.
3. **Given** a client sends `GET /v1/models`, **When** the gateway processes the request, **Then** the response includes `X-API-Version: v1`.

---

### User Story 2 - New integrations can opt into v2 (Priority: P2)

A developer building a new integration targets the v2 path to access endpoints that have breaking changes relative to v1. The gateway routes v2 requests to the appropriate backend behaviour, and responses carry `X-API-Version: v2` so the client can confirm which version served the response.

**Why this priority**: Without a working v2 route, future breaking changes have nowhere to land. The path must exist and be functional before any v2 endpoints are populated.

**Independent Test**: Send a request to a v2-prefixed route; confirm the gateway accepts it, routes correctly, and returns `X-API-Version: v2`. No v2-specific backend logic is required at this stage — a well-formed routing response suffices.

**Acceptance Scenarios**:

1. **Given** a client sends `POST /v2/chat/completions` with a valid payload, **When** the gateway processes the request, **Then** the response status is 200 and the header `X-API-Version: v2` is present.
2. **Given** a client sends a request to any `/v2/` path, **When** the gateway processes the request, **Then** the version header always reflects `v2`, independent of the backend response.
3. **Given** a client sends a request to a non-existent v2 route, **When** the gateway processes the request, **Then** the response status is 404 and `X-API-Version: v2` is still present.

---

### User Story 3 - Deprecated endpoint signals removal date (Priority: P3)

When an endpoint is scheduled for removal, any call to that endpoint returns a standard deprecation warning in the response headers that names the exact removal date (at least 6 months from the deprecation announcement). Integration owners can detect the header programmatically and plan their migration.

**Why this priority**: The deprecation mechanism protects integrators but is only needed when an endpoint is actually being retired. It must be available before any endpoint is deprecated.

**Independent Test**: Mark a test endpoint as deprecated (removal date at least 6 months in the future); call it and confirm the deprecation header is present and contains the removal date.

**Acceptance Scenarios**:

1. **Given** an endpoint has been marked deprecated with a removal date of at least 6 months from today, **When** a client calls that endpoint, **Then** the response includes a `Deprecation` header with the RFC 7231 date and a `Sunset` header with the removal date.
2. **Given** an endpoint is deprecated, **When** a client calls it, **Then** the response also includes a `Link` header pointing to the migration documentation or replacement endpoint.
3. **Given** an endpoint is NOT deprecated, **When** a client calls it, **Then** no `Deprecation` or `Sunset` headers are present.
4. **Given** a deprecation is configured with a removal date fewer than 6 months from today, **When** the configuration is applied, **Then** the gateway rejects the configuration with a clear error message enforcing the 6-month minimum.

---

### Edge Cases

- What happens when a request is sent to `/v3/` or any undefined version prefix? → Gateway returns 404 with a body indicating the version is not supported; `X-API-Version` header is absent.
- What happens if the backend itself sets `X-API-Version`? → The gateway overwrites the header with the authoritative version determined by the route prefix.
- What happens when a deprecated endpoint's `Sunset` date has passed? → The gateway removes the endpoint from routing and returns 410 Gone, with a message directing clients to the replacement.
- What happens when a client sends both `/v1/` prefix and an `Accept-Version: v2` header? → The URL path prefix takes precedence; header-based version negotiation is out of scope.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST route requests prefixed with `/v1/` to the v1 backend surface and inject `X-API-Version: v1` into every response.
- **FR-002**: The gateway MUST route requests prefixed with `/v2/` to the same upstream as v1 (LiteLLM) and inject `X-API-Version: v2` into every response. No separate v2 backend is required.
- **FR-003**: Requests arriving on legacy (unversioned) paths MUST be treated as v1 and receive `X-API-Version: v1` in the response.
- **FR-004**: The `X-API-Version` header set by the gateway MUST overwrite any value of the same header that arrives from upstream services.
- **FR-005**: Requests to unknown version prefixes (e.g., `/v3/`, `/v0/`) MUST return HTTP 404 with a clear error body; no `X-API-Version` header is added.
- **FR-006**: The gateway MUST support marking individual routes as deprecated, specifying a removal date at least 6 months from the deprecation configuration date.
- **FR-007**: Calls to deprecated endpoints MUST include `Deprecation` (RFC 7231 date), `Sunset` (removal date), and `Link` (migration target) headers in the response.
- **FR-008**: The gateway MUST reject any deprecation configuration where the `Sunset` date is fewer than 6 months from today, returning an error to the operator.
- **FR-009**: After a deprecated endpoint's `Sunset` date has passed, the gateway MUST return HTTP 410 Gone instead of proxying the request.
- **FR-010**: Version routing and deprecation configuration MUST be manageable declaratively, consistent with the existing Kong declarative configuration approach.
- **FR-011**: Model name aliases (e.g., `gpt-4o-mini`, `claude-sonnet`, `gemini-flash`) MUST resolve to the same underlying model regardless of whether the request uses a `/v1/` or `/v2/` prefix — clients MUST NOT need to remap model names when adopting v2.

### Key Entities

- **API Version**: A named routing tier (`v1`, `v2`) that shares the same backend upstream (LiteLLM). Differentiates by route prefix and response header only. Has a status (active, deprecated, sunset).
- **Versioned Route**: A route bound to a specific API version prefix. Carries optional deprecation metadata (announcement date, removal date, migration link).
- **Deprecation Record**: Metadata attached to a route: deprecation announcement date, removal (`Sunset`) date, and link to replacement documentation or endpoint.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of existing v1 integrations continue to receive successful responses without any client-side changes after the feature is deployed.
- **SC-002**: Every response from the gateway carries exactly one `X-API-Version` header whose value correctly reflects the version that served the request.
- **SC-003**: All deprecated endpoint responses include `Deprecation`, `Sunset`, and `Link` headers within one gateway round-trip — zero additional latency measured by operators.
- **SC-004**: Any attempt to configure a deprecation with a removal date fewer than 6 months away is rejected 100% of the time before the configuration is applied to live traffic.
- **SC-005**: After a `Sunset` date passes, 100% of calls to that endpoint return 410 Gone with no proxying to the upstream service.
- **SC-006**: Version routing adds no measurable latency to requests compared to the pre-versioning baseline (within noise of existing Kong plugin overhead).

## Assumptions

- Kong is the gateway (already in place per project architecture); versioning is implemented via Kong routes, services, and plugins in declarative configuration.
- The v1 surface maps directly to the current LiteLLM routes; no backend changes to LiteLLM are required for v1.
- The v2 surface routes to the same LiteLLM upstream as v1; both versions are served by the same backend. Version differentiation is gateway-side only (route prefix + response header). Backend-level v2 differences are explicitly out of scope for this feature.
- Model name aliases (e.g., `gpt-4o-mini`, `claude-sonnet`, `gemini-flash`, `command-r-plus`) are version-stable: they map to the same model on both v1 and v2.
- URL path prefix (`/v1/`, `/v2/`) is the versioning strategy; header-based version negotiation (`Accept-Version`) is explicitly out of scope.
- Legacy (unversioned) paths will eventually be deprecated; for now they are aliased to v1 with no deprecation notice.
- The `Link` header on deprecated endpoints points to a documentation URL or a replacement path; the content of that documentation is out of scope.
- Operator-facing configuration changes (adding/modifying deprecation records) follow the existing `make seed-kong` or declarative config reload workflow.
- The 6-month minimum removal notice applies from the date the deprecation is first applied to live traffic, not from any internal draft date.
