"""Active native FastAPI acceptance, inspected after privacy-safe OTLP serialization."""

import threading
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import pytest
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI
from fastapi.telemetry import _runtime
from opentelemetry import _logs, metrics, trace
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind
from otel_http_harness import HTTPHarness
from pydantic import BaseModel, field_serializer
from sqlalchemy.orm import Session

from app.observability import tracing
from app.observability.privacy import NATIVE_SPAN_NAMES


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> HTTPHarness:
    return HTTPHarness(monkeypatch, db_session)


def graph(spans: list[Any]) -> Any:
    servers = [span for span in spans if span.kind == SpanKind.SERVER]
    assert len(servers) == 1
    server = servers[0]
    by_id = {span.context.span_id: span for span in spans}
    assert len(by_id) == len(spans)
    for span in spans:
        assert span.context.trace_id == server.context.trace_id
        visited: set[int] = set()
        while span.context.span_id != server.context.span_id:
            assert span.context.span_id not in visited
            visited.add(span.context.span_id)
            assert span.parent is not None and span.parent.span_id in by_id
            span = by_id[span.parent.span_id]
    return server


def test_native_configuration_owns_no_extra_signals_or_pipeline(
    harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert version("fastapi") == "0.142.2"
    for package in ("opentelemetry-instrumentation-fastapi", "opentelemetry-instrumentation-asgi"):
        with pytest.raises(PackageNotFoundError):
            version(package)
    for signal in ("", "_TRACES", "_METRICS", "_LOGS"):
        monkeypatch.setenv(f"OTEL_EXPORTER_OTLP{signal}_ENDPOINT", "http://PRIVATE_NATIVE:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    configs: list[Any] = []
    native_tracers: list[Any] = []
    original_configure = _runtime._configure_from_environment
    original_tracer = trace.get_tracer

    def configure(config: Any) -> None:
        configs.append(dict(config))
        original_configure(config)

    def get_tracer(*args: Any, **kwargs: Any) -> Any:
        if args[0] == "fastapi":
            native_tracers.append(args[2] if len(args) > 2 else kwargs.get("tracer_provider"))
        return original_tracer(*args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("native metrics/log instruments unexpectedly created")

    monkeypatch.setattr(_runtime, "_configure_from_environment", configure)
    monkeypatch.setattr(trace, "get_tracer", get_tracer)
    monkeypatch.setattr(metrics, "get_meter", forbidden)
    monkeypatch.setattr(_logs, "get_logger", forbidden)
    with harness.start([]) as pipeline:
        assert pipeline.client.get("/health").status_code == 200
        pipeline.flush()
        owned = pipeline.owner.get_tracer_provider()
        assert configs and native_tracers
        for config in configs:
            assert config["tracer_provider"] is owned
            assert config["tracing"] is config["operation_spans"] is True
            assert config["auto_configure"] is config["logs"] is config["metrics"] is False
            assert (
                config["meter_provider"] is config["logger_provider"] is config["exclude"] is None
            )
        assert all(provider is owned for provider in native_tracers)
        assert pipeline.factories == {"provider": 1, "exporter": 1}
        assert len(pipeline.processors) == 1
        assert isinstance(pipeline.processors[0], BatchSpanProcessor)
        assert pipeline.metric_reader.get_metrics_data() is None
        assert pipeline.capture.spans
        assert all(span.instrumentation_scope.name == "fastapi" for span in pipeline.capture.spans)


class PublicResponse(BaseModel):
    ok: bool
    private: str


def test_nested_router_native_operations_execute_once_and_serialize_exclusions(
    harness: HTTPHarness,
) -> None:
    calls: list[str] = []

    def configure(application: FastAPI) -> None:
        child = APIRouter(prefix="/inner")

        def dependency() -> bool:
            calls.append("dependency")
            return True

        @child.get("/native", response_model=PublicResponse, response_model_exclude={"private"})
        def endpoint(ok: bool = Depends(dependency)) -> PublicResponse:
            calls.append("endpoint")
            with tracing.span("agent.run"):
                return PublicResponse(ok=ok, private="PRIVATE_NATIVE_RESPONSE")

        parent = APIRouter(prefix="/outer")
        parent.include_router(child)
        application.include_router(parent, prefix="/test")

    with harness.start([], configure_app=configure) as pipeline:
        response = pipeline.client.get("/test/outer/inner/native")
        assert response.status_code == 200 and response.json() == {"ok": True}
        pipeline.flush()
        spans = pipeline.capture.spans
        server = graph(spans)
        assert server.attributes["http.route"] == "/test/outer/inner/native"
        assert calls == ["dependency", "endpoint"]
        for name in (
            "fastapi.dependencies",
            "fastapi.endpoint",
            "fastapi.serialization",
            "agent.run",
        ):
            assert sum(span.name == name for span in spans) == 1
        native = [span for span in spans if span.name in NATIVE_SPAN_NAMES]
        assert len(native) == 3
        assert all(span.instrumentation_scope.name == "fastapi" for span in native)
        assert all(span.instrumentation_scope.version == "0.142.2" for span in native)
        assert all("code.function.name" not in span.attributes for span in spans)
        pipeline.assert_private_absent("PRIVATE_NATIVE_RESPONSE")


def test_native_background_task_runs_after_response_in_request_context(
    harness: HTTPHarness,
) -> None:
    completed = threading.Event()
    observed: list[int] = []

    def task() -> None:
        assert pipeline.response_sent.is_set()
        with tracing.span("agent.run") as active:
            observed.append(active.get_span_context().trace_id)
        completed.set()

    def configure(application: FastAPI) -> None:
        @application.get("/test/background")
        def endpoint(background_tasks: BackgroundTasks) -> dict[str, bool]:
            background_tasks.add_task(task)
            return {"ok": True}

    with harness.start([], configure_app=configure) as pipeline:
        response = pipeline.client.get("/test/background")
        assert response.status_code == 200 and response.json() == {"ok": True}
        assert completed.wait(timeout=2)
        pipeline.flush()
        server = graph(pipeline.capture.spans)
        assert observed == [server.context.trace_id]
        assert sum(span.name == "fastapi.background_task" for span in pipeline.capture.spans) == 1


@pytest.mark.parametrize("stage", ["dependencies", "endpoint", "serialization", "background_task"])
def test_native_failure_operations_keep_bounded_errors_and_final_payload_private(
    harness: HTTPHarness, stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    secret = f"PRIVATE_NATIVE_{stage}"
    cause = "PRIVATE_NATIVE_CHAINED_CAUSE"

    def fail() -> None:
        try:
            raise ValueError(cause)
        except ValueError as error:
            raise RuntimeError(secret) from error

    class FailingResponse(BaseModel):
        value: str

        @field_serializer("value")
        def serialize_value(self, value: str) -> str:
            if stage == "serialization":
                fail()
            return value

    def configure(application: FastAPI) -> None:
        def dependency() -> None:
            if stage == "dependencies":
                fail()

        @application.get("/test/failure", response_model=FailingResponse)
        def endpoint(
            background_tasks: BackgroundTasks, dependency_value: None = Depends(dependency)
        ) -> FailingResponse:
            if stage == "endpoint":
                fail()
            if stage == "background_task":
                background_tasks.add_task(fail)
            return FailingResponse(value="ok")

    with harness.start([], configure_app=configure) as pipeline:
        response = pipeline.client.get(f"/test/failure?q={secret}")
        assert response.status_code == (200 if stage == "background_task" else 500)
        pipeline.flush()
        spans = pipeline.capture.spans
        graph(spans)
        operations = [span for span in spans if span.name == f"fastapi.{stage}"]
        assert len(operations) == 1 and operations[0].status.status_code.name == "ERROR"
        assert all(span.status.description is None for span in spans)
        for request in pipeline.capture.requests:
            assert all(
                not span.status.message
                for resource in request.resource_spans
                for scoped in resource.scope_spans
                for span in scoped.spans
            )
        pipeline.assert_private_absent(secret, cause)
        assert secret not in caplog.text and cause not in caplog.text


def test_native_disabled_cannot_create_operations_with_external_provider(
    harness: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    external = TracerProvider(shutdown_on_exit=False)
    external.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://PRIVATE_DISABLED:4317")

    def configure(application: FastAPI) -> None:
        @application.get("/test/disabled")
        def endpoint() -> dict[str, bool]:
            with tracing.span("agent.run") as active:
                assert not active.is_recording()
            return {"ok": True}

    try:
        with harness.start(
            [], enabled=False, external=external, configure_app=configure
        ) as pipeline:
            assert pipeline.client.get("/test/disabled").json() == {"ok": True}
            assert pipeline.factories == {"provider": 0, "exporter": 0}
            assert pipeline.processors == [] and pipeline.capture.requests == []
            assert exporter.get_finished_spans() == ()
    finally:
        external.shutdown()
