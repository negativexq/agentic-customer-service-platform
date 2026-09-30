import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core.config import Settings
from app.observability import metrics as application_metrics
from app.observability import tracing


class RecordingProvider(TracerProvider):
    def __init__(self) -> None:
        super().__init__(shutdown_on_exit=False)
        self.events: list[object] = []
        self.flush_result = True
        self.flush_error = False
        self.shutdown_error = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.events.append(("flush", timeout_millis))
        if self.flush_error:
            raise RuntimeError("private flush detail")
        return self.flush_result

    def shutdown(self) -> None:
        self.events.append("shutdown")
        super().shutdown()
        if self.shutdown_error:
            raise RuntimeError("private shutdown detail")


@pytest.fixture
def settings() -> Settings:
    return Settings.model_construct(
        otel_enabled=True,
        otel_service_name="lifecycle-test",
        otel_exporter_otlp_endpoint="http://localhost:4317",
    )


@pytest.fixture
def lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[tracing.ObservabilityLifecycle, list[RecordingProvider], dict[str, Any]]]:
    owner = tracing.ObservabilityLifecycle()
    created: list[RecordingProvider] = []
    registry: dict[str, Any] = {"provider": trace.ProxyTracerProvider()}

    def build(_settings: Settings) -> RecordingProvider:
        provider = RecordingProvider()
        created.append(provider)
        return provider

    monkeypatch.setattr(tracing, "_lifecycle", owner)
    monkeypatch.setattr(tracing, "_build_tracer_provider", build)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: registry["provider"])
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: registry.update(provider=p))
    monkeypatch.setattr(application_metrics, "_metrics", application_metrics.get_metrics())
    yield owner, created, registry
    owner.shutdown()


def test_bootstrap_reuses_one_provider_under_concurrent_calls(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tracing.ObservabilityLifecycle()
    registry: dict[str, Any] = {"provider": trace.ProxyTracerProvider()}
    providers_created: list[TracerProvider] = []
    exporters_created: list[InMemorySpanExporter] = []
    processors_created: list[BatchSpanProcessor] = []
    processors_attached: list[object] = []
    start = Barrier(3)
    exporter_started = Event()
    competing_lock_attempt = Event()
    release_exporter = Event()
    original_lock = owner._lock

    class ObservedLock:
        def __enter__(self) -> None:
            # Observe real contention without replacing the production RLock.
            if not original_lock.acquire(blocking=False):
                competing_lock_attempt.set()
                assert original_lock.acquire(timeout=10)

        def __exit__(self, *_args: object) -> None:
            original_lock.release()

    class CountingProvider(TracerProvider):
        def add_span_processor(self, processor: Any) -> None:
            processors_attached.append(processor)
            super().add_span_processor(processor)

    def provider_factory(**kwargs: Any) -> TracerProvider:
        provider = CountingProvider(**kwargs)
        providers_created.append(provider)
        return provider

    def exporter_factory(**kwargs: Any) -> InMemorySpanExporter:
        assert kwargs == {"endpoint": settings.otel_exporter_otlp_endpoint}
        exporter = InMemorySpanExporter()
        exporters_created.append(exporter)
        exporter_started.set()
        assert release_exporter.wait(timeout=10), "exporter construction was not released"
        return exporter

    def processor_factory(exporter: Any) -> BatchSpanProcessor:
        assert exporter is exporters_created[0]
        processor = BatchSpanProcessor(exporter)
        processors_created.append(processor)
        return processor

    monkeypatch.setattr(owner, "_lock", ObservedLock())
    monkeypatch.setattr(tracing, "_lifecycle", owner)
    # Keep the real _build_tracer_provider: all three construction sites execute.
    monkeypatch.setattr(tracing, "TracerProvider", provider_factory)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", exporter_factory)
    monkeypatch.setattr(tracing, "BatchSpanProcessor", processor_factory)
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: registry["provider"])
    monkeypatch.setattr(trace, "set_tracer_provider", lambda p: registry.update(provider=p))
    monkeypatch.setattr(application_metrics, "_metrics", application_metrics.get_metrics())

    def bootstrap() -> trace.TracerProvider:
        start.wait(timeout=10)
        return tracing.configure_observability(settings)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(bootstrap) for _ in range(2)]
            try:
                start.wait(timeout=10)
                assert exporter_started.wait(timeout=10)
                # One configure call is held inside exporter construction while
                # the other has attempted to acquire its still-held lifecycle lock.
                assert competing_lock_attempt.wait(timeout=10)
                assert len(providers_created) == len(exporters_created) == 1
                assert processors_created == []
            finally:
                release_exporter.set()
            providers = [future.result(timeout=10) for future in futures]
        assert len(providers_created) == 1
        assert len(exporters_created) == 1
        assert len(processors_created) == 1
        assert processors_attached == processors_created
        assert all(provider is providers_created[0] for provider in providers)
        assert tracing.get_tracer_provider() is registry["provider"] is providers_created[0]
        assert tracing.configure_observability(settings) is providers_created[0]
        with tracing.span("application_span") as active_span:
            assert active_span.is_recording()
        assert len(providers_created) == len(exporters_created) == len(processors_created) == 1
    finally:
        release_exporter.set()
        tracing.shutdown_observability()
    assert [span.name for span in exporters_created[0].get_finished_spans()] == ["application_span"]


@pytest.mark.parametrize(
    "update",
    [
        {"otel_service_name": "different"},
        {"otel_exporter_otlp_endpoint": "http://private-endpoint:4317"},
        {"otel_enabled": False},
    ],
)
def test_active_configuration_conflict_does_not_replace_provider(
    settings: Settings, lifecycle: Any, update: dict[str, object]
) -> None:
    owner, created, registry = lifecycle
    provider = owner.configure(settings)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings.model_copy(update=update))
    assert str(error.value) == "telemetry_configuration_conflict"
    assert len(created) == 1
    assert registry["provider"] is provider
    assert created[0].events == []


def test_external_provider_is_rejected_before_pipeline_construction(
    settings: Settings, lifecycle: Any
) -> None:
    owner, created, registry = lifecycle
    external = RecordingProvider()
    registry["provider"] = external
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    owner.shutdown()
    assert str(error.value) == "telemetry_provider_already_registered"
    assert created == []
    assert external.events == []
    external.shutdown()


def test_registration_race_closes_only_unused_owned_provider(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, created, registry = lifecycle
    external = RecordingProvider()
    monkeypatch.setattr(trace, "set_tracer_provider", lambda _: registry.update(provider=external))
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    assert str(error.value) == "telemetry_provider_registration_failed"
    assert created[0].events == ["shutdown"]
    assert external.events == []
    assert registry["provider"] is external
    assert isinstance(owner.get_tracer_provider(), trace.NoOpTracerProvider)
    external.shutdown()


def test_metric_setup_failure_closes_pipeline_and_allows_retry(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, created, registry = lifecycle
    original = application_metrics.configure_metrics

    def fail(_provider: object) -> None:
        raise RuntimeError("private metric setup detail")

    monkeypatch.setattr(tracing, "configure_metrics", fail)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    assert str(error.value) == "telemetry_provider_registration_failed"
    assert created[0].events == ["shutdown"]
    assert isinstance(registry["provider"], trace.ProxyTracerProvider)
    monkeypatch.setattr(tracing, "configure_metrics", original)
    assert owner.configure(settings) is created[1]


@pytest.mark.parametrize("failure", ["none", "false", "flush_exception", "shutdown_exception"])
def test_shutdown_flushes_then_closes_once_even_on_failure(
    settings: Settings, lifecycle: Any, failure: str, caplog: pytest.LogCaptureFixture
) -> None:
    owner, created, _ = lifecycle
    owner.configure(settings)
    provider = created[0]
    provider.flush_result = failure != "false"
    provider.flush_error = failure == "flush_exception"
    provider.shutdown_error = failure == "shutdown_exception"
    tracing.shutdown_observability(timeout_millis=1234)
    tracing.shutdown_observability(timeout_millis=1234)
    assert provider.events == [("flush", 1234), "shutdown"]
    assert isinstance(owner.get_tracer_provider(), trace.NoOpTracerProvider)
    assert "private" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    if failure == "false":
        assert "Telemetry flush did not complete" in caplog.text


def test_shutdown_before_bootstrap_does_not_prevent_startup(
    settings: Settings, lifecycle: Any
) -> None:
    owner, created, _ = lifecycle
    owner.shutdown()
    assert owner.configure(settings) is created[0]


def test_enabled_restart_after_shutdown_requires_new_process(
    settings: Settings, lifecycle: Any
) -> None:
    owner, created, registry = lifecycle
    provider = owner.configure(settings)
    owner.shutdown()
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    assert str(error.value) == "telemetry_restart_requires_new_process"
    assert len(created) == 1
    assert registry["provider"] is provider
    with tracing.span("after_shutdown") as active_span:
        assert not active_span.is_recording()


def test_disabled_domain_telemetry_does_not_use_external_providers(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, created, registry = lifecycle
    exporter = InMemorySpanExporter()
    external = TracerProvider(shutdown_on_exit=False)
    external.add_span_processor(SimpleSpanProcessor(exporter))
    registry["provider"] = external
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: meter_provider)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://private-endpoint:4317")
    disabled = settings.model_copy(update={"otel_enabled": False})
    try:
        before = application_metrics.get_operational_summary()
        for _ in range(2):
            assert isinstance(owner.configure(disabled), trace.NoOpTracerProvider)
            with external.get_tracer("external").start_as_current_span("external_parent"):
                with tracing.span("disabled_domain") as active_span:
                    assert not active_span.is_recording()
                application_metrics.get_metrics().agent_runs_total.add(1)
            owner.shutdown()
        application_metrics.record_agent_run_summary(duration_seconds=0.01, error=False)
        after = application_metrics.get_operational_summary()
        assert after.request_count == before.request_count + 1
        assert created == []
        assert [span.name for span in exporter.get_finished_spans()] == [
            "external_parent",
            "external_parent",
        ]
        assert reader.get_metrics_data() is None
        assert registry["provider"] is external
    finally:
        external.shutdown()
        meter_provider.shutdown()


def test_real_global_registration_context_flush_and_terminal_shutdown() -> None:
    # Use public registration in a fresh process; never reset OTel's private globals.
    script = """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from app.core.config import Settings
from app.observability import tracing

class Exporter(InMemorySpanExporter):
    shutdowns = 0
    def shutdown(self):
        self.shutdowns += 1

exporter = Exporter()
provider = TracerProvider(shutdown_on_exit=False)
provider.add_span_processor(BatchSpanProcessor(exporter))
tracing._build_tracer_provider = lambda settings: provider
settings = Settings.model_construct(otel_enabled=True)
assert tracing.configure_observability(settings) is provider
assert tracing.configure_observability(settings) is provider
assert trace.get_tracer_provider() is tracing.get_tracer_provider() is provider
with trace.get_tracer("framework").start_as_current_span("http") as parent:
    with tracing.span("agent.run") as child:
        assert child.get_span_context().trace_id == parent.get_span_context().trace_id
tracing.shutdown_observability()
tracing.shutdown_observability()
spans = {span.name: span for span in exporter.get_finished_spans()}
assert spans["agent.run"].parent.span_id == spans["http"].context.span_id
assert exporter.shutdowns == 1
try:
    tracing.configure_observability(settings)
except tracing.ObservabilityConfigurationError as error:
    assert str(error) == "telemetry_restart_requires_new_process"
else:
    raise AssertionError("closed provider was reused")
"""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("OTEL_")}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_exporter_construction_failure_closes_partial_provider(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider()
    monkeypatch.setattr(tracing, "TracerProvider", lambda **_: provider)

    def fail(**_kwargs: object) -> None:
        raise RuntimeError("private exporter configuration")

    monkeypatch.setattr(tracing, "OTLPSpanExporter", fail)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        tracing._build_tracer_provider(settings)
    assert str(error.value) == "telemetry_initialization_failed"
    assert provider.events == ["shutdown"]


def test_failed_construction_does_not_claim_ownership_and_can_retry(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, created, registry = lifecycle
    original = tracing._build_tracer_provider

    def fail(_settings: Settings) -> TracerProvider:
        raise RuntimeError("private initialization detail")

    monkeypatch.setattr(tracing, "_build_tracer_provider", fail)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    assert str(error.value) == "telemetry_initialization_failed"
    assert created == []
    assert isinstance(registry["provider"], trace.ProxyTracerProvider)
    monkeypatch.setattr(tracing, "_build_tracer_provider", original)
    assert owner.configure(settings) is created[0]


def test_registration_exception_after_global_install_is_terminal(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, created, registry = lifecycle

    def register(provider: TracerProvider) -> None:
        registry["provider"] = provider
        raise RuntimeError("private registration detail")

    monkeypatch.setattr(trace, "set_tracer_provider", register)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        owner.configure(settings)
    assert str(error.value) == "telemetry_provider_registration_failed"
    assert registry["provider"] is created[0]
    assert created[0].events == ["shutdown"]
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="telemetry_restart_requires_new_process"
    ):
        owner.configure(settings)


def test_lifespan_rejects_closed_provider_before_allocating_resources(
    settings: Settings, lifecycle: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI

    from app import main

    owner, _, _ = lifecycle
    owner.configure(settings)
    owner.shutdown()
    monkeypatch.setattr(main, "settings", settings)

    def fail_if_called(_settings: Settings) -> None:
        pytest.fail("checkpoint allocation preceded telemetry lifecycle validation")

    monkeypatch.setattr(main, "build_checkpoint_provider", fail_if_called)

    async def startup() -> None:
        async with main.lifespan(FastAPI()):
            pytest.fail("closed telemetry provider allowed startup")

    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="telemetry_restart_requires_new_process"
    ):
        asyncio.run(startup())


def test_processor_construction_failure_closes_exporter_and_provider(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = RecordingProvider()
    exporter = InMemorySpanExporter()
    closed: list[str] = []
    monkeypatch.setattr(tracing, "TracerProvider", lambda **_: provider)
    monkeypatch.setattr(tracing, "OTLPSpanExporter", lambda **_: exporter)
    monkeypatch.setattr(exporter, "shutdown", lambda: closed.append("exporter"))

    def fail(_exporter: object) -> None:
        raise RuntimeError("private processor detail")

    monkeypatch.setattr(tracing, "BatchSpanProcessor", fail)
    with pytest.raises(tracing.ObservabilityConfigurationError) as error:
        tracing._build_tracer_provider(settings)
    assert str(error.value) == "telemetry_initialization_failed"
    assert closed == ["exporter"]
    assert provider.events == ["shutdown"]
