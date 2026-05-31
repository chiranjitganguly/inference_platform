# Feature Specification: Per-Consumer Gateway Rate Limiting

**Feature Branch**: `013-consumer-rate-limiting`

**Created**: 2026-05-30

**Status**: Draft

**Input**: Build per-consumer rate limiting at the gateway so no single API key can saturate the platform. Each consumer key must be independently limited by requests per second, per minute, and per hour. When a limit is exceeded the gateway must return a throttle response with a Retry-After header. One consumer reaching its limit must not affect other consumers.

---

## Clarifications

### Session 2026-05-30

- Q: What are the concrete default rate-limit values per consumer? → A: 10 req/s, 300 req/min, 10,000 req/hr; the 11th request in the same second is the first to be rejected.
- Q: Where are rate-limit counters stored and how are they shared across gateway instances? → A: Counters are stored in the shared Redis instance; all Kong instances read and write the same counters, ensuring consistency across horizontal scaling.
- Q: Are limits enforced per consumer identity (API key) or per source IP address? → A: Per consumer identity (API key). Source IP is irrelevant; multiple clients sharing the same key share one counter set.
- Q: Are time windows fixed/tumbling (calendar-aligned) or sliding/rolling? → A: **Fixed/tumbling** (calendar-aligned). Initially clarified as sliding, but overridden at planning stage: Kong OSS 3.6 `rate-limiting` plugin implements fixed windows only; sliding windows require Kong Enterprise (`rate-limiting-advanced`), which is out of scope. See `research.md §1`.
- Q: Should rate-limit events emit Prometheus metrics, and at what granularity? → A: Yes, per-consumer metrics — emit allowed and rejected request counters labelled by consumer key and window type (second/minute/hour).
- Q: When shared Redis is unavailable, should the gateway fail open (allow requests) or fail closed (503 all requests)? → A: Fail open — requests pass through without rate-limit checks; a Prometheus alert fires immediately so operators are notified.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Throttled Consumer Receives Actionable Error (Priority: P1)

An API consumer sends requests at a rate that exceeds one of their configured limits (per-second, per-minute, or per-hour). Instead of receiving an opaque failure, the consumer receives a clear throttle response that tells them exactly how long to wait before retrying.

**Why this priority**: Graceful throttling is the core deliverable. Without it consumers experience silent failures or confusing errors, and the platform has no defence against a single key saturating all capacity.

**Independent Test**: Can be fully tested by sending rapid requests with a single API key until a limit is exceeded, confirming a 429 response with a `Retry-After` header is returned — while a second API key continues to receive 200 responses unaffected.

**Acceptance Scenarios**:

1. **Given** a consumer key with a per-second limit of 10, **When** the consumer sends the 11th request within the same second, **Then** the gateway returns HTTP 429 with a `Retry-After` header indicating seconds until the per-second window resets.
2. **Given** a consumer key with a per-minute limit of 300, **When** the 301st request arrives within the same minute window, **Then** the gateway returns HTTP 429 with a `Retry-After` header and the response body describes that the minute quota is exhausted.
3. **Given** a consumer key with a per-hour limit of 10,000, **When** the 10,001st request arrives within the same hour window, **Then** the gateway returns HTTP 429 with a `Retry-After` header pointing to when the hour window resets.
4. **Given** a consumer is throttled at the per-second limit, **When** the per-second window resets, **Then** the consumer can immediately resume sending requests up to the limit without any manual intervention.

---

### User Story 2 - Consumer Isolation Under Saturation (Priority: P1)

Consumer A sends traffic at a rate that exhausts its own limits. Consumer B, operating within its own independent limits, continues to receive successful responses with no increase in latency or error rate.

**Why this priority**: Isolation is the safety invariant. Multi-tenant value is lost if one misbehaving key degrades service for all others.

**Independent Test**: Can be fully tested by running two concurrent clients — one hammering past its limit and one sending at a normal rate — and confirming the normal client's success rate remains at 100 % while the hammering client receives 429s.

**Acceptance Scenarios**:

1. **Given** Consumer A is actively receiving 429 responses due to exhausted limits, **When** Consumer B sends a request within its own unused quota, **Then** Consumer B receives a 200 response with no added latency.
2. **Given** 10 consumers all simultaneously hitting their individual limits, **When** an 11th consumer with unused quota sends a request, **Then** the 11th consumer receives a 200 response.
3. **Given** two clients using the same API key make requests from different IP addresses, **When** their combined request count exceeds 10 in one second, **Then** the 11th request (regardless of source IP) is rejected with 429 — both clients share the same counter.

---

### User Story 3 - Operator Configures Per-Consumer Limits (Priority: P2)

A platform operator assigns different rate-limit tiers to different API keys — for example, a free-tier key gets tighter limits than an enterprise key — without restarting the gateway or affecting in-flight traffic.

**Why this priority**: Without configurable tiers the platform cannot monetise usage, enforce fair-use policies, or grant elevated access to trusted partners.

**Independent Test**: Can be fully tested by updating a consumer's limit configuration and immediately verifying the new limit takes effect for subsequent requests while existing consumers are unaffected.

**Acceptance Scenarios**:

1. **Given** an API key configured at the free-tier limits, **When** an operator upgrades it to enterprise-tier limits, **Then** the key's new limits apply to the very next request with no service interruption.
2. **Given** a newly issued API key with no explicit limit assignment, **When** the key sends its first request, **Then** the platform applies the default rate-limit policy (10 req/s, 300 req/min, 10,000 req/hr) and the request succeeds if within those limits.
3. **Given** an operator sets a per-consumer per-hour limit to zero, **When** that consumer sends any request, **Then** every request is rejected with 429.

---

### User Story 4 - Consumers Observe Remaining Quota (Priority: P3)

An API consumer checks response headers after each successful call to understand how much quota remains in each window, enabling the consumer to self-throttle before hitting a hard limit.

**Why this priority**: Quota visibility reduces unnecessary 429s and support requests, but the core rate-limiting function works without it.

**Independent Test**: Can be fully tested by making a single request and inspecting the response headers for quota-remaining and quota-limit values across each time window.

**Acceptance Scenarios**:

1. **Given** a consumer with a per-minute limit of 300 who has made 30 requests, **When** the 30th response is returned, **Then** response headers indicate 270 remaining calls in the current minute window.
2. **Given** a consumer whose per-hour quota is fully exhausted, **When** the consumer inspects the 429 response headers, **Then** the headers indicate 0 remaining calls and the reset time.

---

### Edge Cases

- When all three limits are exceeded simultaneously, the `Retry-After` value is the longest remaining reset time across all exhausted windows (the soonest moment the consumer can actually succeed, not just the first window to reset).
- How does the system handle a request that arrives at the exact boundary tick of a time window (e.g., the 10,000th request at T=3,599 seconds into the hour)?
- What happens if the shared Redis counter store becomes temporarily unavailable — does the gateway fail open (allow all) or fail closed (block all)?
- How are rate-limit counters affected by a rolling gateway restart during high traffic?
- What happens when a consumer's API key is rotated — do counters reset or carry over?
- How does the gateway behave when a consumer sends a burst of requests simultaneously (e.g., 50 parallel requests against a limit of 10/s)?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The gateway MUST maintain independent rate-limit counters per consumer identity (API key). Source IP address MUST NOT influence counter assignment; multiple clients sharing one key share one counter set.
- **FR-002**: Each consumer key MUST be subject to three simultaneously enforced fixed (calendar-aligned) time-window limits: 10 requests per second, 300 requests per minute, and 10,000 requests per hour (default policy values). Windows reset at calendar boundaries (top of each second/minute/hour).
- **FR-003**: When any one of the three limits is exceeded, the gateway MUST reject the request immediately with an HTTP 429 status code before the request reaches any downstream service.
- **FR-004**: Every HTTP 429 throttle response MUST include a `Retry-After` header whose value is the number of whole seconds until the most restrictive exhausted fixed window resets at its next calendar boundary (i.e., the soonest moment the consumer can make a successful request across all exhausted windows).
- **FR-005**: Every HTTP 429 throttle response MUST include a human-readable body describing which limit window was exceeded.
- **FR-006**: Every successful response (2xx) MUST include headers indicating the consumer's remaining quota for each time window and the reset timestamp for each window.
- **FR-007**: Rate-limit counters MUST be stored in the shared Redis instance and read/written by all gateway instances, ensuring counter state is consistent across horizontal scaling and survives individual gateway process restarts.
- **FR-008**: Per-consumer limit values (per-second, per-minute, per-hour) MUST be configurable independently for each consumer key without requiring a gateway restart.
- **FR-009**: A default rate-limit policy of 10 req/s, 300 req/min, and 10,000 req/hr MUST apply automatically to any consumer key that has no explicit limit configuration assigned.
- **FR-010**: The gateway MUST enforce rate limits before performing any model routing, so throttled requests consume no downstream resources.
- **FR-011**: The gateway MUST emit two Prometheus counters per rate-limit decision: one for allowed requests and one for rejected requests, both labelled by consumer key and the window type that triggered the decision (second, minute, or hour). These metrics MUST be scrapeable by the existing Prometheus instance.
- **FR-012**: When the shared Redis store is unreachable, the gateway MUST fail open — requests are forwarded to downstream services without rate-limit enforcement. The gateway MUST simultaneously emit a Prometheus alert-triggering metric indicating the degraded state, so operators are notified within one alert evaluation interval.

### Key Entities

- **Consumer**: An authenticated API key identity; has zero or one associated rate-limit policy; maintains three independent counters (per-second, per-minute, per-hour); counter keyed on API key, not source IP.
- **Rate-Limit Policy**: A named configuration object defining maximum allowed requests for each of the three time windows; default policy is 10/s, 300/min, 10,000/hr; can be assigned to one or many consumers.
- **Rate-Limit Counter**: An integer count of requests made by a specific consumer within a fixed calendar window; stored in shared Redis so all gateway instances share the same view; expires automatically at the window boundary (TTL = window duration).
- **Throttle Response**: The HTTP 429 reply issued when any counter exceeds its policy limit; contains the `Retry-After` duration (longest exhausted window reset) and quota exhaustion details.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A consumer exceeding any rate limit receives an HTTP 429 response with a `Retry-After` header within the same round-trip latency as a normal 200 response (no added processing delay visible to the caller).
- **SC-002**: While Consumer A is fully throttled (100 % of requests returning 429), Consumer B operating within its own quota achieves a 100 % success rate with no measurable latency increase.
- **SC-003**: Under horizontal scaling with multiple gateway instances, the same API key's counter is consistent — the 11th request in one second is rejected regardless of which gateway instance handles it.
- **SC-004**: An operator can change a consumer's rate-limit tier and the new limits are enforced on the very next request, with zero downtime.
- **SC-005**: Under a 10-consumer concurrent saturation test, no consumer's throttle state bleeds into any other consumer's quota counters.
- **SC-006**: The default policy (10/s, 300/min, 10,000/hr) is enforced for any consumer with no explicit configuration; a consumer sending 11 requests in one second with the default policy receives exactly 1 rejected request.
- **SC-007**: Prometheus metrics for allowed and rejected requests are visible per consumer key within one scrape interval of the event occurring, enabling a Grafana dashboard panel to display per-consumer throttle rates in real time.

---

## Assumptions

- Rate-limit counters are stored in the existing Redis instance (port 6379) already deployed in the platform stack; all Kong instances connect to the same Redis, providing a single source of truth for counter state. No additional data store is introduced.
- The feature builds directly on the Kong consumer and key-auth setup established in phase 012 (feature `012-kong-api-gateway-auth`); consumer identities already exist in Kong.
- Time windows are **fixed/tumbling** (calendar-aligned): second resets at :00.000, minute at :00, hour at :00:00 UTC. When all three windows are breached simultaneously, `Retry-After` reflects the longest remaining time to the next calendar boundary across all exhausted windows — the soonest moment the consumer can actually make a successful request. Note: fixed windows allow a worst-case boundary burst of up to 20 requests/second at a window straddle (accepted trade-off; see `research.md §1`).
- When the shared Redis store is unreachable, the gateway fails open (allows requests without rate-limit enforcement) to preserve availability. This is an intentional posture for a non-authentication control; a Prometheus alert notifies operators of the degraded state. Fail-closed behaviour is explicitly out of scope.
- Key rotation (issuing a new API key) results in a fresh counter set for the new key; the old key's counters are retired with the old key.
- Rate limiting applies to all routes passing through Kong at port 8080 and is enforced before the request is forwarded to Guardrails or LiteLLM.
- Consumers are expected to respect `Retry-After` headers and implement exponential back-off; the platform does not enforce client-side behaviour.
