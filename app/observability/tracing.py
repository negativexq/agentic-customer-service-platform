from __future__ import annotations

import atexit
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from app.core.config import Settings
from app.observability.attributes import set_safe_attributes
from app.observability.export import PrivacyOTLPSpanExporter
from app.observability.metrics import configure_metrics, disable_metrics
from app.observability.privacy import safe_exception_type

logger = logging.getLogger(__name__)
_NOOP_PROVIDER = trace.NoOpTracerProvider()


class ObservabilityConfigurationError(RuntimeError):
    """A bounded failure to establish the application's telemetry ownership."""


def _close_provider(provider: TracerProvider) -> None:
    try:
        provider.shutdown()
    except Exception as error:
        logger.warning(
            "Telemetry provider shutdown failed.",
            extra={"telemetry_error_type": type(error).__name__},
        )


def _build_tracer_provider(settings: Settings) -> TracerProvider:
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name}),
        shutdown_on_exit=False,
    )
    exporter: PrivacyOTLPSpanExporter | None = None
    processor: BatchSpanProcessor | None = None
    try:
        exporter = PrivacyOTLPSpanExporter(
            endpoint=settings.otel_exporter_otlp_endpoint, service_name=settings.otel_service_name
        )
        processor = BatchSpanProcessor(exporter)
        provider.add_span_processor(processor)
    except Exception:
        # Components not yet attached to the provider still need cleanup.
        component = processor or exporter
        if component is not None:
            try:
                component.shutdown()
            except Exception as error:
                logger.warning(
                    "Telemetry initialization cleanup failed.",
                    extra={"telemetry_error_type": type(error).__name__},
                )
        _close_provider(provider)
        raise ObservabilityConfigurationError("telemetry_initialization_failed") from None
    return provider


class ObservabilityLifecycle:
    """Own one trace pipeline per process; serialize bootstrap and shutdown."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._provider: TracerProvider | None = None
        self._configuration: tuple[str, str] | None = None
        self._closed = False

    def get_tracer_provider(self) -> trace.TracerProvider:
        with self._lock:
            return self._provider if self._provider is not None else _NOOP_PROVIDER

    def configure(self, settings: Settings) -> trace.TracerProvider:
        with self._lock:
            if not settings.otel_enabled:
                if self._provider is not None:
                    raise ObservabilityConfigurationError("telemetry_configuration_conflict")
                disable_metrics()
                return _NOOP_PROVIDER
            if self._closed:
                raise ObservabilityConfigurationError("telemetry_restart_requires_new_process")
            configuration = (settings.otel_service_name, settings.otel_exporter_otlp_endpoint)
            if self._provider is not None:
                if configuration != self._configuration:
                    raise ObservabilityConfigurationError("telemetry_configuration_conflict")
                if trace.get_tracer_provider() is not self._provider:
                    raise ObservabilityConfigurationError("telemetry_provider_ownership_conflict")
                return self._provider
            if not isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
                raise ObservabilityConfigurationError("telemetry_provider_already_registered")

            try:
                provider = _build_tracer_provider(settings)
            except Exception:
                disable_metrics()
                raise ObservabilityConfigurationError("telemetry_initialization_failed") from None
            try:
                # This does not create a metric reader or exporter. T4 owns that work.
                configure_metrics(metrics.get_meter_provider())
                trace.set_tracer_provider(provider)
                if trace.get_tracer_provider() is not provider:
                    raise ObservabilityConfigurationError("telemetry_provider_registration_failed")
            except Exception:
                # Global registration cannot be undone through the public OTel API.
                self._closed = trace.get_tracer_provider() is provider
                _close_provider(provider)
                disable_metrics()
                raise ObservabilityConfigurationError(
                    "telemetry_provider_registration_failed"
                ) from None
            self._provider = provider
            self._configuration = configuration
            return provider

    def shutdown(self, timeout_millis: int = 5000) -> None:
        with self._lock:
            provider = self._provider
            if provider is None:
                return
            # Detach first so repeated/reentrant shutdown cannot close it twice.
            self._provider = None
            self._closed = True
            disable_metrics()
            try:
                if not provider.force_flush(timeout_millis=timeout_millis):
                    logger.warning(
                        "Telemetry flush did not complete during shutdown.",
                        extra={"telemetry_status": "flush_incomplete"},
                    )
            except Exception as error:
                logger.warning(
                    "Telemetry flush failed during shutdown.",
                    extra={"telemetry_error_type": type(error).__name__},
                )
            finally:
                _close_provider(provider)


_lifecycle = ObservabilityLifecycle()


def configure_observability(settings: Settings) -> trace.TracerProvider:
    return _lifecycle.configure(settings)


def get_tracer_provider() -> trace.TracerProvider:
    """Return the application provider, or a no-op while disabled/closed."""

    return _lifecycle.get_tracer_provider()


def shutdown_observability(timeout_millis: int = 5000) -> None:
    """Flush and close only the tracer provider owned by this application."""

    _lifecycle.shutdown(timeout_millis)


atexit.register(shutdown_observability)


def tracer(name: str = "agentic-customer-service-platform") -> trace.Tracer:
    return get_tracer_provider().get_tracer(name)


@contextmanager
def span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        name, record_exception=False, set_status_on_exception=False
    ) as active_span:
        if attributes:
            set_safe_attributes(active_span, attributes)
        try:
            yield active_span
        except BaseException as error:
            active_span.set_status(Status(StatusCode.ERROR))
            active_span.add_event(
                "application.exception", attributes={"error.type": safe_exception_type(error)}
            )
            raise
