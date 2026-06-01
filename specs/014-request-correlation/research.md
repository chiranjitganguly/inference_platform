# Research: Request Correlation (014)

## R-001 — Kong correlation-id plugin overwrite behaviour

**Decision**: Use a global `pre-function` plugin to strip client-supplied `X-Request-ID`, `traceparent`, and `tracestate` headers before the `correlation-id` plugin runs.

**Rationale**: Kong's `correlation-id` plugin (priority 100001) does NOT overwrite an existing `X-Request-ID` header — it passes the client value through unchanged. To guarantee the gateway always emits its own UUID, the headers must be cleared in an earlier phase. The `pre-function` serverless plugin runs at priority 1000000, giving it guaranteed pre-execution before `correlation-id`. Three `kong.request.clear_header(...)` calls in the `access` Lua function accomplish this with no performance cost.

**Alternatives considered**:
- _request-transformer (remove)_: priority 801, executes well after `correlation-id` (100001) — cannot strip before UUID generation.
- _Disable correlation-id, use opentelemetry trace ID_: OpenTelemetry trace IDs are 128-bit hex; `X-Request-ID` must be a UUID per spec. Mismatched formats would break SC-005 (single-ID lookup).
- _Custom Kong plugin_: unnecessary complexity given `pre-function` is available in Kong 3.6 OSS.

---

## R-002 — Kong OpenTelemetry plugin for W3C traceparent/tracestate

**Decision**: Enable the Kong built-in `opentelemetry` plugin globally. Configure it to forward spans to the OTel Collector at `http://otel-collector:4318/v1/traces`. Set `propagation.default_format = "w3c"` to generate and inject `traceparent`/`tracestate` headers into every upstream request.

**Rationale**: Kong 3.6 ships the `opentelemetry` plugin in its OSS image. When activated, it:
1. Creates a root span for each inbound request (with `service.name = kong-gateway`).
2. Injects W3C `traceparent` (format `00-<trace_id>-<span_id>-01`) into the upstream request so Phoenix Arize can correlate the LiteLLM child span to the gateway root span.
3. Captures HTTP request headers — including `X-Request-ID` (set by `correlation-id` before OTel runs) — as span attributes following OTel HTTP semantic conventions (`http.request.header.x_request_id`).
4. Exports spans via OTLP HTTP to the OTel Collector.

Client-supplied `traceparent`/`tracestate` are already cleared by the `pre-function` plugin before the OTel plugin runs, so the gateway always anchors its own root span.

**Alternatives considered**:
- _request-transformer to manually inject traceparent_: cannot generate a cryptographically valid W3C trace ID; results in a static or predictable header.
- _Kong Zipkin plugin_: Zipkin B3 format, not W3C. Phoenix Arize requires W3C for span correlation.
- _LiteLLM generates its own traceparent_: LiteLLM is downstream of guardrails; spans would not be parented to the Kong root span, breaking end-to-end trace correlation in Phoenix.

---

## R-003 — OTel Collector deployment and X-Request-ID span enrichment

**Decision**: Add the OTel Collector (`otel/opentelemetry-collector-contrib:0.97.0`) as a new service in the `obs` Docker Compose profile. Configure it with:
- **Receiver**: `otlp` (HTTP :4318, gRPC :4317)
- **Processor**: `attributes` — insert `gateway.request_id` from `http.request.header.x_request_id` span attribute (emitted by Kong OTel plugin)
- **Processor**: `batch` — reduces Phoenix write pressure
- **Exporter**: `otlp/phoenix` — forward to `http://arize-phoenix:6006` (Phoenix OTLP ingestion endpoint)
- **Exporter**: `prometheus` — expose `/metrics` for Grafana scrape on :8889

**Rationale**: The OTel Collector currently is not deployed (the Arize Phoenix container accepts OTLP directly from LiteLLM). Introducing the Collector between Kong and Phoenix allows the `attributes` processor to promote `http.request.header.x_request_id` → `gateway.request_id` on every span, satisfying FR-010 (queryable X-Request-ID trace attribute). LiteLLM's existing spans are re-routed through the Collector so they also receive the enrichment.

**Note on attribute extraction**: Kong's OTel plugin emits HTTP request headers as span attributes under the naming convention `http.request.header.<lowercased_header_name_with_underscores>`. For `X-Request-ID` this produces `http.request.header.x_request_id`. The `attributes` processor copies this to the canonical `gateway.request_id` attribute.

**Alternatives considered**:
- _Direct Kong → Phoenix OTLP_: No intermediate processing possible; cannot enrich spans with X-Request-ID without a collector stage.
- _Phoenix native attribute extraction_: Phoenix Arize does not support server-side attribute processors on inbound OTLP; enrichment must happen before ingestion.
- _OTel Agent (per-service sidecar)_: Requires sidecar injection into each container; inconsistent with this platform's Docker Compose topology.

---

## R-004 — Plugin execution order in Kong 3.6

**Decision**: Plugin execution order (highest priority runs first in access phase):

| Plugin | Priority | Role |
|---|---|---|
| pre-function | 1000000 | Strip client X-Request-ID, traceparent, tracestate |
| correlation-id | 100001 | Generate UUID, set X-Request-ID on request + response |
| opentelemetry | 100000 | Generate traceparent/tracestate, inject into upstream request, emit span |
| key-auth | 1003 | Authenticate consumer |
| rate-limiting | 901 | Enforce per-consumer limits |
| request-transformer | 801 | Add platform headers (existing response-transformer: 800) |
| response-transformer | 800 | Add X-Platform, X-API-Version |

The pre-function plugin strips client headers first, then correlation-id generates a fresh UUID on the clean request, then opentelemetry creates a root span that includes the now-set X-Request-ID as a span attribute.

**Alternatives considered**:
- Running pre-function in `rewrite` phase instead of `access`: Both phases precede upstream proxying; `access` is preferred as it is the standard phase for header manipulation.

---

## R-005 — OTel Collector impact on existing LiteLLM → Phoenix trace path

**Decision**: Reroute LiteLLM's OTLP export through the Collector. Update `OTEL_EXPORTER_OTLP_ENDPOINT` from `http://arize-phoenix:6006/v1/traces` to `http://otel-collector:4318/v1/traces`. The Collector forwards to Phoenix with enrichment.

**Rationale**: LiteLLM's spans currently go directly to Phoenix. Routing them through the Collector ensures they also receive the `gateway.request_id` attribute when the Collector can extract it from span attributes forwarded by Kong. Where LiteLLM spans carry the `traceparent` injected by Kong, the trace linkage flows automatically.

**Alternatives considered**:
- _Keep LiteLLM → Phoenix direct, add Kong → Collector → Phoenix_: Two separate OTLP pipelines into Phoenix; spans from Kong and LiteLLM would not share enrichment. The Collector's attribute processor would only touch Kong-origin spans, leaving LiteLLM spans without `gateway.request_id`.
