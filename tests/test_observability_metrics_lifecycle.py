"""Metrics lifecycle extends T2A ownership without global meter registration."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from typing import Any, cast

import grpc
import pytest
from opentelemetry import metrics, trace
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceResponse,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.trace import TracerProvider

from app.core.config import Settings
from app.observability import metric_export, metric_provider, tracing
from app.observability import metrics as application_metrics
from app.observability.metric_export import PrivacyOTLPMetricExporter
from app.observability.metric_provider import PrivacyMeterProvider


class TraceProvider(TracerProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.closed_count = 0
        self.flush_count = 0
        self.flush_error = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flush_count += 1
        if self.flush_error:
            raise RuntimeError("PRIVATE_TRACE_FLUSH")
        return True

    def shutdown(self) -> None:
        self.closed_count += 1
        super().shutdown()


class MetricWire:
    def __init__(self) -> None:
        self.requests: list[bytes] = []
        self.closes = 0

    def Export(self, request: Any, **kwargs: Any) -> ExportMetricsServiceResponse:
        self.requests.append(request.SerializeToString())
        return ExportMetricsServiceResponse()

    def close(self) -> None:
        self.closes += 1


@pytest.fixture
def setup(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[
    tuple[tracing.ObservabilityLifecycle, dict[str, Any], list[TraceProvider], MetricWire]
]:
    owner = tracing.ObservabilityLifecycle()
    registry: dict[str, Any] = {"provider": trace.ProxyTracerProvider()}
    providers: list[TraceProvider] = []
    wire = MetricWire()

    def build(_: Settings) -> TraceProvider:
        provider = TraceProvider()
        providers.append(provider)
        return provider

    monkeypatch.setattr(tracing, "_lifecycle", owner)
    monkeypatch.setattr(tracing, "_build_tracer_provider", build)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: registry["provider"])
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: registry.update(provider=p))
    monkeypatch.setattr(grpc, "insecure_channel", lambda *_a, **_k: wire)
    monkeypatch.setattr(metric_export, "MetricsServiceStub", lambda _: wire)
    monkeypatch.setattr(application_metrics, "_metrics", application_metrics.get_metrics())
    try:
        yield owner, registry, providers, wire
    finally:
        owner.shutdown()


def settings(**update: Any) -> Settings:
    return Settings.model_construct(
        otel_enabled=True,
        otel_metrics_enabled=True,
        otel_exporter_otlp_metrics_endpoint="http://collector:4317",
        otel_metric_export_interval_millis=300000,
        **update,
    )


def test_concurrent_bootstrap_invokes_each_metric_factory_once(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, registry, traces, wire = setup
    start = Barrier(3)
    constructing = Event()
    contender = Event()
    release = Event()
    lock = owner._lock

    class ObservedLock:
        def __enter__(self) -> None:
            if not lock.acquire(blocking=False):
                contender.set()
                assert lock.acquire(timeout=10)

        def __exit__(self, *_args: object) -> None:
            lock.release()

    monkeypatch.setattr(owner, "_lock", ObservedLock())
    counts = {"provider": 0, "reader": 0, "exporter": 0}
    original_exporter = PrivacyOTLPMetricExporter
    original_reader = PeriodicExportingMetricReader
    original_sdk = MeterProvider

    def exporter(**kwargs: Any) -> Any:
        counts["exporter"] += 1
        instance = original_exporter(**kwargs)
        constructing.set()
        assert release.wait(10)
        return instance

    def reader(*args: Any, **kwargs: Any) -> Any:
        counts["reader"] += 1
        return original_reader(*args, **kwargs)

    def sdk(**kwargs: Any) -> Any:
        counts["provider"] += 1
        return original_sdk(**kwargs)

    monkeypatch.setattr(metric_provider, "PrivacyOTLPMetricExporter", exporter)
    monkeypatch.setattr(metric_provider, "PeriodicExportingMetricReader", reader)
    monkeypatch.setattr(metric_provider, "SDKMeterProvider", sdk)
    configuration = settings()

    def configure() -> object:
        start.wait(10)
        tracing.configure_observability(configuration)
        return tracing.get_meter_provider()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(configure) for _ in range(2)]
            try:
                start.wait(10)
                assert constructing.wait(10)
                assert contender.wait(10)
                assert counts == {"provider": 0, "reader": 0, "exporter": 1}
            finally:
                release.set()
            shared = [f.result(10) for f in futures]
        assert counts == {"provider": 1, "reader": 1, "exporter": 1}
        assert shared[0] is shared[1] is owner.get_meter_provider()
        assert len(traces) == 1 and registry["provider"] is traces[0]
        assert isinstance(shared[0], PrivacyMeterProvider)
        assert isinstance(shared[0].sdk, MeterProvider)
        application_metrics.get_metrics().agent_runs_total.add(1)
        assert shared[0].force_flush(5000)
        assert len(wire.requests) == 1
        owner.configure(configuration)
        assert counts == {"provider": 1, "reader": 1, "exporter": 1}
    finally:
        release.set()
        owner.shutdown()


@pytest.mark.parametrize("enabled,metric_enabled", [(False, False), (False, True), (True, False)])
def test_disabled_metric_modes_do_not_construct_or_adopt_external_meter(
    setup: Any, monkeypatch: pytest.MonkeyPatch, enabled: bool, metric_enabled: bool
) -> None:
    owner, _, _, wire = setup
    reader = InMemoryMetricReader()
    external = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: external)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "http://PRIVATE_ENDPOINT:4317")
    monkeypatch.setattr(
        tracing,
        "build_metric_provider",
        lambda *_: pytest.fail("disabled metrics constructed pipeline"),
    )
    before = application_metrics.get_operational_summary()
    try:
        owner.configure(
            settings().model_copy(
                update={"otel_enabled": enabled, "otel_metrics_enabled": metric_enabled}
            )
        )
        application_metrics.get_metrics().agent_runs_total.add(1)
        application_metrics.record_agent_run_summary(duration_seconds=0.1, error=False)
        owner.shutdown()
        assert wire.requests == []
        assert reader.get_metrics_data() is None
        external.get_meter("external").create_counter("external.counter").add(1)
        assert reader.get_metrics_data() is not None
        assert (
            application_metrics.get_operational_summary().request_count == before.request_count + 1
        )
    finally:
        external.shutdown()


@pytest.mark.parametrize(
    "update",
    [
        {"otel_exporter_otlp_metrics_endpoint": ""},
        {"otel_exporter_otlp_metrics_endpoint": "http://PRIVATE@collector:4317"},
        {"otel_metric_export_interval_millis": 0},
        {"otel_metric_export_timeout_millis": 0},
    ],
)
def test_invalid_metric_config_fails_before_any_factory(
    setup: Any, update: dict[str, object]
) -> None:
    owner, registry, traces, wire = setup
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="^telemetry_initialization_failed$"
    ) as error:
        owner.configure(settings().model_copy(update=update))
    assert error.value.__cause__ is None
    assert not traces and not wire.requests and wire.closes == 0
    assert isinstance(registry["provider"], trace.ProxyTracerProvider)


@pytest.mark.parametrize(
    "update",
    [
        {"otel_metrics_enabled": False},
        {"otel_exporter_otlp_metrics_endpoint": "http://different:4317"},
        {"otel_metric_export_interval_millis": 15000},
        {"otel_metric_export_timeout_millis": 1000},
    ],
)
def test_metric_configuration_conflict_reuses_original_pipeline(
    setup: Any, update: dict[str, object]
) -> None:
    owner, _, traces, _ = setup
    original = settings()
    owner.configure(original)
    provider = owner.get_meter_provider()
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="^telemetry_configuration_conflict$"
    ):
        owner.configure(original.model_copy(update=update))
    assert owner.get_meter_provider() is provider
    assert len(traces) == 1


@pytest.mark.parametrize("stage", ["reader", "sdk", "binding", "registration"])
def test_partial_initialization_closes_owned_components_without_export(
    setup: Any, monkeypatch: pytest.MonkeyPatch, stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    owner, registry, traces, wire = setup

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        application_metrics.get_metrics().agent_runs_total.add(1)
        raise RuntimeError("PRIVATE_INITIALIZATION_DETAIL")

    if stage in {"reader", "sdk"}:
        monkeypatch.setattr(
            metric_provider,
            "PeriodicExportingMetricReader" if stage == "reader" else "SDKMeterProvider",
            fail,
        )
    elif stage == "binding":
        monkeypatch.setattr(tracing, "configure_metrics", fail)
    else:
        monkeypatch.setattr(trace, "set_tracer_provider", fail)
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="^telemetry_provider_registration_failed$"
    ):
        owner.configure(settings())
    owner.shutdown()
    assert traces[0].closed_count == 1
    assert wire.closes == 1 and wire.requests == []
    assert isinstance(owner.get_meter_provider(), metrics.NoOpMeterProvider)
    assert isinstance(registry["provider"], trace.ProxyTracerProvider)
    assert "PRIVATE" not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


@pytest.mark.parametrize(
    "failure", ["trace_flush", "metric_flush_false", "metric_flush_exception", "metric_shutdown"]
)
def test_shutdown_attempts_both_signals_once_and_stops_old_instruments(
    setup: Any, monkeypatch: pytest.MonkeyPatch, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    owner, registry, traces, wire = setup
    config = settings()
    owner.configure(config)
    provider = owner.get_meter_provider()
    assert isinstance(provider, PrivacyMeterProvider)
    old = application_metrics.get_metrics()
    old.agent_runs_total.add(1)
    events: list[str] = []
    shutdown = provider.sdk.shutdown

    def close(timeout_millis: float = 30000) -> None:
        events.append("shutdown")
        shutdown(timeout_millis=timeout_millis)
        if failure == "metric_shutdown":
            raise RuntimeError("PRIVATE_SHUTDOWN")

    def flush(timeout_millis: float = 10000) -> bool:
        events.append("flush")
        if failure == "metric_flush_exception":
            raise RuntimeError("PRIVATE_FLUSH")
        return failure != "metric_flush_false"

    monkeypatch.setattr(provider.sdk, "shutdown", close)
    monkeypatch.setattr(provider.sdk, "force_flush", flush)
    traces[0].flush_error = failure == "trace_flush"
    owner.shutdown(1000)
    owner.shutdown(1000)
    assert events == ["flush", "shutdown"]
    assert traces[0].flush_count == traces[0].closed_count == 1
    assert isinstance(owner.get_meter_provider(), metrics.NoOpMeterProvider)
    count = len(wire.requests)
    old.agent_runs_total.add(1)
    application_metrics.get_metrics().agent_runs_total.add(1)
    provider.shutdown()
    assert len(wire.requests) == count
    assert registry["provider"] is traces[0]
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="telemetry_restart_requires_new_process"
    ):
        owner.configure(config)
    assert "PRIVATE" not in caplog.text and all(r.exc_info is None for r in caplog.records)


def test_external_meter_is_never_registered_adopted_or_closed_when_enabled(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, _, _, wire = setup
    reader = InMemoryMetricReader()
    external = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: external)
    monkeypatch.setattr(
        metrics, "set_meter_provider", lambda *_: pytest.fail("global meter registration")
    )
    try:
        owner.configure(settings())
        assert owner.get_meter_provider() is not external
        application_metrics.get_metrics().agent_runs_total.add(1)
        owner.shutdown()
        assert wire.requests
        assert reader.get_metrics_data() is None
        external.get_meter("external").create_counter("external.counter").add(1)
        assert reader.get_metrics_data() is not None
    finally:
        external.shutdown()


def test_exporter_stub_construction_failure_closes_allocated_channel_and_trace(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    owner, registry, traces, wire = setup
    allocated = Event()

    def channel(*_args: Any, **_kwargs: Any) -> MetricWire:
        allocated.set()
        return cast(MetricWire, wire)

    def fail_stub(_channel: object) -> None:
        assert allocated.is_set()
        raise RuntimeError("PRIVATE_STUB_CONSTRUCTION_CREDENTIAL")

    monkeypatch.setattr(grpc, "insecure_channel", channel)
    monkeypatch.setattr(metric_export, "MetricsServiceStub", fail_stub)
    monkeypatch.setattr(
        metric_provider,
        "PeriodicExportingMetricReader",
        lambda *_a, **_k: pytest.fail("reader constructed after exporter failure"),
    )
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="^telemetry_provider_registration_failed$"
    ):
        owner.configure(settings())
    owner.shutdown()
    owner.shutdown()
    assert allocated.is_set() and wire.closes == 1 and wire.requests == []
    assert traces[0].closed_count == 1 and traces[0].flush_count == 0
    assert isinstance(registry["provider"], trace.ProxyTracerProvider)
    assert isinstance(owner.get_meter_provider(), metrics.NoOpMeterProvider)
    assert "PRIVATE" not in caplog.text and all(r.exc_info is None for r in caplog.records)


def test_metric_enabled_failure_after_trace_registration_is_terminal_and_cleans_once(
    setup: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    owner, registry, traces, wire = setup
    constructed: list[PrivacyMeterProvider] = []
    original = metric_provider.build_metric_provider

    def build(configuration: Settings) -> PrivacyMeterProvider:
        instance = original(configuration)
        constructed.append(instance)
        return instance

    monkeypatch.setattr(tracing, "build_metric_provider", build)

    def register(provider: trace.TracerProvider) -> None:
        registry["provider"] = provider
        application_metrics.get_metrics().agent_runs_total.add(1)
        raise RuntimeError("PRIVATE_AFTER_GLOBAL_REGISTRATION")

    monkeypatch.setattr(trace, "set_tracer_provider", register)
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="^telemetry_provider_registration_failed$"
    ):
        owner.configure(settings())
    assert len(constructed) == 1 and constructed[0].closed
    assert registry["provider"] is traces[0]
    assert traces[0].closed_count == 1 and wire.closes == 1 and not wire.requests
    assert isinstance(owner.get_tracer_provider(), trace.NoOpTracerProvider)
    assert isinstance(owner.get_meter_provider(), metrics.NoOpMeterProvider)
    owner.shutdown()
    owner.shutdown()
    constructed[0].shutdown()
    for _ in range(2):
        with pytest.raises(
            tracing.ObservabilityConfigurationError,
            match="^telemetry_restart_requires_new_process$",
        ):
            owner.configure(settings())
    assert len(constructed) == len(traces) == 1
    assert traces[0].closed_count == wire.closes == 1
    assert registry["provider"] is traces[0]
    assert "PRIVATE" not in caplog.text and all(r.exc_info is None for r in caplog.records)
