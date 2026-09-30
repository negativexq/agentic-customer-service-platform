"""Public API recording adapters around one application-owned SDK provider."""

from __future__ import annotations

from collections.abc import Sequence
from threading import RLock

from opentelemetry.context import Context
from opentelemetry.metrics import (
    Counter,
    Histogram,
    Meter,
    MeterProvider,
    NoOpCounter,
    NoOpHistogram,
    NoOpMeter,
    NoOpUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter
from opentelemetry.sdk.metrics import MeterProvider as SDKMeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.util.types import Attributes

from app.core.config import Settings
from app.observability.metric_export import PrivacyOTLPMetricExporter
from app.observability.metric_privacy import (
    CATALOG,
    SCOPES,
    labels_for,
    scope_for,
    valid_measurement,
)


class _Recording:
    def __init__(
        self, owner: PrivacyMeterProvider, name: str, delegate: Counter | Histogram | UpDownCounter
    ) -> None:
        self.owner = owner
        self.name = name
        self.delegate = delegate

    def record(self, amount: int | float, attributes: Attributes, context: Context | None) -> None:
        with self.owner.lock:
            if (
                self.owner.closed
                or not self.owner.recording_enabled
                or not valid_measurement(self.name, amount)
            ):
                return
            labels = labels_for(self.name, attributes)
            if labels is None:
                return
            if isinstance(self.delegate, Histogram):
                self.delegate.record(amount, labels, context)
            else:
                self.delegate.add(amount, labels, context)


class _Counter(Counter):
    def __init__(self, recording: _Recording) -> None:
        self.recording = recording

    def add(
        self, amount: int | float, attributes: Attributes = None, context: Context | None = None
    ) -> None:
        self.recording.record(amount, attributes, context)


class _UpDownCounter(UpDownCounter):
    def __init__(self, recording: _Recording) -> None:
        self.recording = recording

    def add(
        self, amount: int | float, attributes: Attributes = None, context: Context | None = None
    ) -> None:
        self.recording.record(amount, attributes, context)


class _Histogram(Histogram):
    def __init__(self, recording: _Recording) -> None:
        self.recording = recording

    def record(
        self, amount: int | float, attributes: Attributes = None, context: Context | None = None
    ) -> None:
        self.recording.record(amount, attributes, context)


class _Meter(NoOpMeter):
    """Unsupported synchronous/observable instruments remain public no-ops."""

    def __init__(self, owner: PrivacyMeterProvider, name: str, delegate: Meter) -> None:
        super().__init__(name)
        self.owner = owner
        self.delegate = delegate
        self.instruments: dict[str, Counter | Histogram | UpDownCounter] = {}

    def _create(self, kind: str, name: str) -> Counter | Histogram | UpDownCounter:
        with self.owner.lock:
            spec = CATALOG.get(name)
            if (
                self.owner.closed
                or spec is None
                or spec.kind != kind
                or scope_for(name) != self.name
            ):
                # Never retain client-supplied names/metadata, even in no-op objects.
                if kind == "counter":
                    return NoOpCounter("unknown")
                if kind == "histogram":
                    return NoOpHistogram("unknown")
                return NoOpUpDownCounter("unknown")
            if name not in self.instruments:
                delegate: Counter | Histogram | UpDownCounter
                if kind == "counter":
                    delegate = self.delegate.create_counter(name, spec.unit, spec.description)
                    wrapper: type[_Counter] | type[_Histogram] | type[_UpDownCounter] = _Counter
                elif kind == "histogram":
                    boundaries = (
                        (0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1, 2.5, 5, 7.5, 10)
                        if name == "http.server.request.duration"
                        else None
                    )
                    delegate = self.delegate.create_histogram(
                        name,
                        spec.unit,
                        spec.description,
                        explicit_bucket_boundaries_advisory=boundaries,
                    )
                    wrapper = _Histogram
                else:
                    delegate = self.delegate.create_up_down_counter(
                        name, spec.unit, spec.description
                    )
                    wrapper = _UpDownCounter
                self.instruments[name] = wrapper(_Recording(self.owner, name, delegate))
            return self.instruments[name]

    def create_counter(self, name: str, unit: str = "", description: str = "") -> Counter:
        instrument = self._create("counter", name)
        assert isinstance(instrument, Counter)
        return instrument

    def create_up_down_counter(
        self, name: str, unit: str = "", description: str = ""
    ) -> UpDownCounter:
        instrument = self._create("up_down_counter", name)
        assert isinstance(instrument, UpDownCounter)
        return instrument

    def create_histogram(
        self,
        name: str,
        unit: str = "",
        description: str = "",
        *,
        explicit_bucket_boundaries_advisory: Sequence[float] | None = None,
    ) -> Histogram:
        instrument = self._create("histogram", name)
        assert isinstance(instrument, Histogram)
        return instrument


class PrivacyMeterProvider(MeterProvider):
    """Explicitly supplied facade; no global provider adoption/registration."""

    def __init__(self, sdk: SDKMeterProvider, *, recording_enabled: bool = True) -> None:
        self.sdk = sdk
        self.lock = RLock()
        self.closed = False
        self.recording_enabled = recording_enabled
        self._shutdown = False
        self.meters: dict[str, Meter] = {}

    def get_meter(
        self,
        name: str,
        version: str | None = None,
        schema_url: str | None = None,
        attributes: Attributes = None,
    ) -> Meter:
        with self.lock:
            if self.closed or name not in SCOPES:
                return NoOpMeter("unknown")
            if name not in self.meters:
                self.meters[name] = _Meter(self, name, self.sdk.get_meter(name))
            return self.meters[name]

    def stop_recording(self) -> None:
        with self.lock:
            self.closed = True

    def activate(self) -> None:
        with self.lock:
            if not self.closed:
                self.recording_enabled = True

    def force_flush(self, timeout_millis: float = 10000) -> bool:
        return self.sdk.force_flush(timeout_millis=timeout_millis)

    def shutdown(self, timeout_millis: float = 30000) -> None:
        self.stop_recording()
        with self.lock:
            if self._shutdown:
                return
            self._shutdown = True
        self.sdk.shutdown(timeout_millis=timeout_millis)


def build_metric_provider(settings: Settings) -> PrivacyMeterProvider:
    """Close each unattached component on failure, without global registration."""
    exporter = PrivacyOTLPMetricExporter(
        endpoint=settings.otel_exporter_otlp_metrics_endpoint,
        service_name=settings.otel_service_name,
        timeout_millis=settings.otel_metric_export_timeout_millis,
    )
    reader: PeriodicExportingMetricReader | None = None
    sdk: SDKMeterProvider | None = None
    try:
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=settings.otel_metric_export_interval_millis,
            export_timeout_millis=settings.otel_metric_export_timeout_millis,
        )
        sdk = SDKMeterProvider(
            metric_readers=[reader],
            resource=Resource.create({"service.name": settings.otel_service_name}),
            exemplar_filter=AlwaysOffExemplarFilter(),
            shutdown_on_exit=False,
        )
        return PrivacyMeterProvider(sdk, recording_enabled=False)
    except Exception:
        try:
            if sdk is not None:
                sdk.shutdown()
            elif reader is not None:
                reader.shutdown()
            else:
                exporter.shutdown()
        except Exception:
            pass  # Bootstrap reports a fixed failure, without config/exception content.
        raise
