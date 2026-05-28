"""Custom LiteLLM callback that adds llm.fallback.* span attributes to Phoenix traces."""
from __future__ import annotations

import logging
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("fallback_callbacks")


class FallbackSpanCallback(CustomLogger):
    """Adds llm.fallback.* OpenInference span attributes for fallback routing events."""

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self._record_fallback_attributes(kwargs, success=True)

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self._record_fallback_attributes(kwargs, success=False)

    async def async_log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self._record_fallback_attributes(kwargs, success=True)

    async def async_log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        self._record_fallback_attributes(kwargs, success=False)

    def _record_fallback_attributes(self, kwargs: dict[str, Any], success: bool) -> None:
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            if span is None or not span.is_recording():
                return

            metadata: dict[str, Any] = kwargs.get("metadata") or {}
            model_group: str = metadata.get("model_group") or kwargs.get("model", "")
            fulfilled_model: str = kwargs.get("model", "")
            fallback_triggered = bool(model_group) and model_group != fulfilled_model

            span.set_attribute("llm.model.requested", model_group or fulfilled_model)
            span.set_attribute("llm.fallback.triggered", fallback_triggered)

            if fallback_triggered:
                fallback_attempts: list = metadata.get("fallback_attempts") or []
                span.set_attribute("llm.fallback.attempt_count", len(fallback_attempts) + 1)

            if not success:
                exc = kwargs.get("exception")
                span.set_attribute("llm.fallback.reason", self._classify_reason(exc))

        except Exception as exc:  # noqa: BLE001
            logger.debug("FallbackSpanCallback: could not record span attributes: %s", exc)

    @staticmethod
    def _classify_reason(exc: Any) -> str:
        if exc is None:
            return "provider_error"
        exc_type = type(exc).__name__
        exc_str = str(exc).lower()
        if "contextwindowexceeded" in exc_type or "context_length" in exc_str:
            return "context_overflow"
        if "timeout" in exc_type.lower() or "timeout" in exc_str:
            return "timeout"
        return "provider_error"
