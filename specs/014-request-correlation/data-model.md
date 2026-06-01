# Data Model: Request Correlation (014)

This feature introduces no new database tables or Redis keys. All correlation data is ephemeral — it lives only in request/response headers, structured log fields, and OTel span attributes.

---

## Header Data Flow

```
Client Request
  │
  ▼
Kong :8080 [pre-function]
  │  STRIP: X-Request-ID (client-supplied)
  │  STRIP: traceparent  (client-supplied)
  │  STRIP: tracestate   (client-supplied)
  │
  ▼
Kong [correlation-id plugin]
  │  GENERATE: X-Request-ID = UUID v4
  │  SET on request (forwarded upstream) + response (echo_downstream: true)
  │
  ▼
Kong [opentelemetry plugin]
  │  GENERATE: traceparent = "00-{trace_id}-{span_id}-01"  (W3C v00)
  │  GENERATE: tracestate  = ""  (omitted when empty)
  │  INJECT into upstream request headers
  │  EMIT OTel span to otel-collector:4318
  │     span attributes:
  │       service.name                       = "kong-gateway"
  │       http.request.header.x_request_id   = <UUID>
  │       http.method, http.url, http.status_code, ...
  │
  ▼
OTel Collector :4318
  │  ENRICH: gateway.request_id = http.request.header.x_request_id
  │  BATCH spans
  │  FORWARD to arize-phoenix:6006 (OTLP HTTP)
  │
  ▼
Guardrails :8088
  │  RECEIVES: X-Request-ID header  → written to audit log field `request_id`
  │  RECEIVES: traceparent header   → propagated to LiteLLM call
  │
  ▼
LiteLLM :4000
  │  RECEIVES: X-Request-ID header
  │  RECEIVES: traceparent header   → passed to OTEL_EXPORTER
  │  EMITS OTel span to otel-collector:4318
  │     span attributes include traceparent parent (Phoenix links as child of Kong span)
  │
  ▼
Phoenix Arize :6006
     STORES spans queryable by:
       - W3C trace_id  (from traceparent)
       - gateway.request_id  (from OTel enrichment = X-Request-ID UUID)
```

---

## Span Attribute Schema

### Kong Gateway Span (emitted by Kong opentelemetry plugin)

| Attribute | Value | Notes |
|---|---|---|
| `service.name` | `kong-gateway` | Set in plugin resource config |
| `http.request.header.x_request_id` | UUID string | Captured from request header set by correlation-id |
| `gateway.request_id` | UUID string | Added by OTel Collector attributes processor (alias) |
| `http.method` | GET / POST | OTel HTTP semantic convention |
| `http.url` | Request URL | OTel HTTP semantic convention |
| `http.status_code` | 200 / 401 / 429 / 503 | OTel HTTP semantic convention |
| `http.route` | /v1/chat/completions | Route matched by Kong |

### LiteLLM Span (emitted by LiteLLM via arize_phoenix callback, enriched by OTel Collector)

| Attribute | Value | Notes |
|---|---|---|
| `service.name` | `litellm` | |
| `gateway.request_id` | UUID string | Added by OTel Collector attributes processor |
| `llm.vendor` | openai / anthropic / google | OpenInference convention |
| `llm.model_name` | gpt-4o-mini / claude-sonnet / ... | |
| Prompt/response content | **NEVER stored** | Constitution §II — hard constraint |

---

## Log Field Schema (Loki)

Audit log entries written by the guardrails service include the propagated `X-Request-ID`:

```json
{
  "timestamp": "2026-06-01T12:00:00.000Z",
  "event_type": "inference_request",
  "request_id": "<UUID>",
  "key_hash": "<sha256_of_api_key>",
  "model_name": "gpt-4o-mini",
  "pii_entity_count": 0,
  "scanner_blocked": false
}
```

No new fields are added to this schema. `request_id` must already be populated from the forwarded `X-Request-ID` header.

---

## Response Header Contract

Every HTTP response from Kong MUST include:

| Header | Format | Example |
|---|---|---|
| `X-Request-ID` | UUID v4, lowercase hex with hyphens | `f47ac10b-58cc-4372-a567-0e02b2c3d479` |
| `X-Platform` | Literal string | `inference-platform` |
| `X-API-Version` | Literal string | `1` |

Client-supplied `X-Request-ID` in the request is always absent from any upstream request and always absent from the response (replaced by gateway UUID).

---

## Entities (no persistence)

| Entity | Lifetime | Carrier |
|---|---|---|
| **Correlation ID** | Single request | `X-Request-ID` header (request + response) |
| **W3C Trace Context** | Single request | `traceparent` + `tracestate` headers |
| **Gateway Span** | Until Phoenix writes to disk | OTel span over OTLP |
| **LLM Span** | Until Phoenix writes to disk | OTel span (child of gateway span via traceparent) |
