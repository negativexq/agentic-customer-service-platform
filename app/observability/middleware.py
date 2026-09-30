from fastapi.telemetry import TelemetryConfig
from opentelemetry.trace import TracerProvider

from app.core.config import Settings


def fastapi_telemetry(settings: Settings, provider: TracerProvider) -> TelemetryConfig:
    """Use the application-owned trace pipeline without native export/log/metric setup."""
    return {
        "tracer_provider": provider,
        "tracing": settings.otel_enabled,
        "operation_spans": settings.otel_enabled,
        "auto_configure": False,
        "logs": False,
        "metrics": False,
    }
