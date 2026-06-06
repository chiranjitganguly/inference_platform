"""OpenTelemetry tracer setup for the batch-worker service."""
from __future__ import annotations

import json
import os

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_tracer: trace.Tracer | None = None

_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4318")
_SERVICE = os.getenv("OTEL_SERVICE_NAME", "batch-worker")


def init_tracer() -> trace.Tracer:
    global _tracer
    if _tracer is not None:
        return _tracer
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=f"{_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    set_global_textmap(TraceContextTextMapPropagator())
    _tracer = provider.get_tracer(_SERVICE)
    return _tracer


def get_tracer() -> trace.Tracer:
    if _tracer is None:
        return init_tracer()
    return _tracer


def inject_context(span: trace.Span) -> str:
    """Serialise the span's trace context to a W3C traceparent carrier JSON string."""
    carrier: dict[str, str] = {}
    ctx = trace.set_span_in_context(span)
    propagate.inject(carrier, context=ctx)
    return json.dumps(carrier)


def extract_context(carrier_json: str) -> Context:
    """Deserialise a carrier JSON string back to an OTel Context."""
    if not carrier_json:
        return Context()
    try:
        return propagate.extract(json.loads(carrier_json))
    except Exception:
        return Context()
