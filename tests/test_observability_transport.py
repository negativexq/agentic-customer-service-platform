"""Public gRPC transport contract tests for the application privacy exporter."""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import grpc
import pytest
from google.rpc.error_details_pb2 import RetryInfo
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTracePartialSuccess,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
    TraceServiceServicer,
    add_TraceServiceServicer_to_server,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from otel_capture import WireExporter

from app.observability import export as module
from app.observability.export import PrivacyOTLPSpanExporter


@pytest.fixture(autouse=True)
def clean_otel_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP_"):
            monkeypatch.delenv(name)


class RPCFailure(grpc.RpcError):  # type: ignore[misc] # grpc ships no typed RpcError base
    def __init__(self, code: grpc.StatusCode, retry_info: bytes | None = None) -> None:
        self.result = code
        self.retry_info = retry_info

    def code(self) -> grpc.StatusCode:
        return self.result

    def details(self) -> str:
        return "PRIVATE_RPC_ERROR_DETAILS"

    def trailing_metadata(self) -> tuple[tuple[str, bytes], ...]:
        return (("google.rpc.retryinfo-bin", self.retry_info),) if self.retry_info else ()


@pytest.mark.parametrize(
    "code",
    [
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.OUT_OF_RANGE,
        grpc.StatusCode.DATA_LOSS,
    ],
)
def test_retryable_failure_reuses_sanitized_request_and_deadline(
    monkeypatch: pytest.MonkeyPatch,
    code: grpc.StatusCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    capture = WireExporter()
    capture.install(monkeypatch)
    exporter = capture.exporter(service_name="test")
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(random, "uniform", lambda *a: 1.0)
    waits: list[float] = []

    def wait(delay: float) -> bool:
        waits.append(delay)
        now[0] += delay
        return False

    monkeypatch.setattr(exporter._stopped, "wait", wait)
    requests: list[bytes] = []
    timeouts: list[float] = []

    def send(request: Any, **kwargs: Any) -> ExportTraceServiceResponse:
        requests.append(request.SerializeToString())
        timeouts.append(kwargs["timeout"])
        if len(requests) == 1:
            raise RPCFailure(code)
        return ExportTraceServiceResponse()

    monkeypatch.setattr(capture, "Export", send)
    raw = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(raw))
    with provider.get_tracer("agentic-customer-service-platform").start_as_current_span(
        "agent.run"
    ) as active:
        active.set_attribute("prompt", "PRIVATE_RETRY_PAYLOAD")
    try:
        assert exporter.export(raw.get_finished_spans()) == SpanExportResult.SUCCESS
    finally:
        provider.shutdown()
    assert all(b"PRIVATE_RETRY_PAYLOAD" not in request for request in requests)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert waits == [1.0] and timeouts == [10.0, 9.0]
    assert capture.shutdowns == (1 if code == grpc.StatusCode.UNAVAILABLE else 0)
    exporter.shutdown()
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize(
    "code",
    [
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNKNOWN,
    ],
)
def test_nonretryable_errors_fail_once_without_sensitive_logging(
    monkeypatch: pytest.MonkeyPatch,
    code: grpc.StatusCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    capture = WireExporter()
    exporter = capture.exporter(service_name="test")
    calls: list[int] = []

    def send(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        raise RPCFailure(code)

    monkeypatch.setattr(capture, "Export", send)
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert calls == [1]
    assert "PRIVATE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
    exporter.shutdown()


def test_retry_info_and_six_attempt_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = WireExporter()
    capture.install(monkeypatch)
    exporter = capture.exporter(service_name="test")
    now = [0.0]
    waits: list[float] = []
    monkeypatch.setattr(module, "monotonic", lambda: now[0])

    def wait(delay: float) -> bool:
        waits.append(delay)
        now[0] += delay
        return False

    monkeypatch.setattr(exporter._stopped, "wait", wait)
    calls: list[float] = []
    info = RetryInfo()
    info.retry_delay.nanos = 250_000_000

    def send(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["timeout"])
        raise RPCFailure(grpc.StatusCode.RESOURCE_EXHAUSTED, info.SerializeToString())

    monkeypatch.setattr(capture, "Export", send)
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert waits == [0.25] * 5
    assert calls == [10, 9.75, 9.5, 9.25, 9, 8.75]
    exporter.shutdown()


def test_retry_does_not_exceed_timeout_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.1")
    capture = WireExporter()
    exporter = capture.exporter(service_name="test")
    calls: list[int] = []

    def send(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        raise RPCFailure(grpc.StatusCode.DEADLINE_EXCEEDED)

    monkeypatch.setattr(capture, "Export", send)
    monkeypatch.setattr(random, "uniform", lambda *a: 1.0)
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert calls == [1]
    exporter.shutdown()


def test_shutdown_interrupts_backoff_and_closes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = WireExporter()
    exporter = capture.exporter(service_name="test")
    waiting = Event()
    original_wait = exporter._stopped.wait

    def wait(delay: float) -> bool:
        waiting.set()
        return original_wait(delay)

    monkeypatch.setattr(exporter._stopped, "wait", wait)

    def send(*a: Any, **kw: Any) -> Any:
        raise RPCFailure(grpc.StatusCode.RESOURCE_EXHAUSTED)

    monkeypatch.setattr(capture, "Export", send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(exporter.export, [])
        assert waiting.wait(5)
        exporter.shutdown()
        assert future.result(timeout=5) == SpanExportResult.FAILURE
    exporter.shutdown()
    assert capture.shutdowns == 1
    assert exporter.export([]) == SpanExportResult.FAILURE


def test_flush_waits_for_active_export_and_honors_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = WireExporter()
    exporter = capture.exporter(service_name="test")
    started, release = Event(), Event()

    def send(*a: Any, **kw: Any) -> Any:
        started.set()
        assert release.wait(5)
        return ExportTraceServiceResponse()

    monkeypatch.setattr(capture, "Export", send)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(exporter.export, [])
        try:
            assert started.wait(5)
            assert not exporter.force_flush(0)
        finally:
            release.set()
        assert future.result(timeout=5) == SpanExportResult.SUCCESS
    assert exporter.force_flush(1000)
    exporter.shutdown()


def test_tls_headers_compression_and_environment_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    capture = WireExporter()
    capture.install(monkeypatch)
    options: dict[str, Any] = {}
    cert = tmp_path / "cert"
    cert.write_bytes(b"test-certificate")
    for suffix in ("CERTIFICATE", "CLIENT_KEY", "CLIENT_CERTIFICATE"):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_" + suffix, str(cert))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "ignored=global")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "authorization=Bearer%20PRIVATE_TOKEN")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "20")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "3")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_COMPRESSION", "gzip")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_INSECURE", "true")

    def credentials(**kw: Any) -> Any:
        options["credentials"] = kw
        return "credentials"

    def channel(target: str, credentials: Any, **kw: Any) -> Any:
        options.update(target=target, **kw)
        return capture

    monkeypatch.setattr(grpc, "ssl_channel_credentials", credentials)
    monkeypatch.setattr(grpc, "secure_channel", channel)
    exporter = PrivacyOTLPSpanExporter(endpoint="https://jaeger:4317", service_name="test")
    assert options["target"] == "jaeger:4317"
    assert options["compression"] == grpc.Compression.Gzip
    assert options["credentials"] == {
        "root_certificates": b"test-certificate",
        "private_key": b"test-certificate",
        "certificate_chain": b"test-certificate",
    }
    assert exporter.export([]) == SpanExportResult.SUCCESS
    assert capture.calls[0]["metadata"] == (("authorization", "Bearer PRIVATE_TOKEN"),)
    assert 0 < capture.calls[0]["timeout"] <= 3
    exporter.shutdown()


def test_partial_rejection_fails_without_retry_or_response_text(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    capture = WireExporter()
    exporter = capture.exporter(service_name="test")
    calls: list[int] = []

    def send(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        return ExportTraceServiceResponse(
            partial_success=ExportTracePartialSuccess(
                rejected_spans=1, error_message="PRIVATE_SERVER_RESPONSE"
            )
        )

    monkeypatch.setattr(capture, "Export", send)
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert calls == [1]
    assert "PRIVATE" not in caplog.text
    exporter.shutdown()


def test_real_grpc_server_receives_one_sanitized_multispan_batch() -> None:
    received: list[Any] = []

    class Receiver(TraceServiceServicer):
        def Export(self, request: Any, context: Any) -> ExportTraceServiceResponse:
            received.append(request)
            return ExportTraceServiceResponse()

    server = grpc.server(ThreadPoolExecutor(max_workers=1))
    add_TraceServiceServicer_to_server(Receiver(), server)  # type: ignore[no-untyped-call]
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    exporter = PrivacyOTLPSpanExporter(endpoint=f"http://127.0.0.1:{port}", service_name="test")
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=60000))
    try:
        tracer = provider.get_tracer("agentic-customer-service-platform")
        with tracer.start_as_current_span("agent.run") as parent:
            parent.set_attribute("prompt", "PRIVATE_GRPC_SENTINEL")
            with tracer.start_as_current_span("tool.execute"):
                pass
        assert provider.force_flush(5000)
        assert len(received) == 1
        assert (
            sum(
                len(scope.spans)
                for resource in received[0].resource_spans
                for scope in resource.scope_spans
            )
            == 2
        )
        assert b"PRIVATE_GRPC_SENTINEL" not in received[0].SerializeToString()
    finally:
        provider.shutdown()
        server.stop(0).wait()


@pytest.mark.parametrize("timeout", ["nan", "inf", "0", "-1", "PRIVATE_INVALID_CONFIG"])
def test_invalid_timeout_rejected_before_channel_creation(
    monkeypatch: pytest.MonkeyPatch,
    timeout: str,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", timeout)

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("channel opened before configuration validation")

    monkeypatch.setattr(grpc, "insecure_channel", unexpected)
    with pytest.raises(ValueError):
        PrivacyOTLPSpanExporter(endpoint="http://localhost:4317", service_name="test")


def test_stub_initialization_failure_closes_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    capture = WireExporter()
    capture.install(monkeypatch)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("PRIVATE_STUB_INITIALIZATION")

    monkeypatch.setattr(module, "TraceServiceStub", fail)
    with pytest.raises(RuntimeError):
        PrivacyOTLPSpanExporter(endpoint="http://localhost:4317", service_name="test")
    assert capture.shutdowns == 1
