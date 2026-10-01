from fastapi.telemetry import TelemetryConfig
from opentelemetry.metrics import MeterProvider
from opentelemetry.trace import TracerProvider

from app.core.config import Settings
from app.observability.metric_provider import PrivacyMeterProvider


def fastapi_telemetry(
    settings: Settings, provider: TracerProvider, meter_provider: MeterProvider | None = None
) -> TelemetryConfig:
    """Bind native signals to successfully initialized application-owned providers."""
    metric_enabled = (
        settings.otel_enabled
        and settings.otel_metrics_enabled
        and isinstance(meter_provider, PrivacyMeterProvider)
        and meter_provider.recording_enabled
        and not meter_provider.closed
    )
    return {
        "tracer_provider": provider,
        "tracing": settings.otel_enabled,
        "operation_spans": settings.otel_enabled,
        "auto_configure": False,
        "logs": False,
        "metrics": metric_enabled,
        "meter_provider": meter_provider if metric_enabled else None,
    }
