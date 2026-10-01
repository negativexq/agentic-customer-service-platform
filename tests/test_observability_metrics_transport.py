"""Metric transport parity and actual public gRPC delivery, independent of Collector."""

from __future__ import annotations

import random
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import grpc
import pytest
from google.rpc.error_details_pb2 import RetryInfo
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsPartialSuccess,
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
    MetricsServiceServicer,
    add_MetricsServiceServicer_to_server,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Metric,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope

from app.core.config import Settings
from app.observability import metric_export as module
from app.observability.metric_export import PrivacyOTLPMetricExporter
from app.observability.metric_provider import build_metric_provider
from app.observability.metrics import build_metrics


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP_"):
            monkeypatch.delenv(name)


def data() -> MetricsData:
    point = NumberDataPoint({}, 1, 2, 3)
    metric = Metric(
        "agent_runs_total",
        "PRIVATE_DESCRIPTION",
        "PRIVATE_UNIT",
        Sum([point], AggregationTemporality.CUMULATIVE, True),
    )
    return MetricsData(
        [
            ResourceMetrics(
                Resource({"host.name": "PRIVATE_HOST"}),
                [
                    ScopeMetrics(
                        InstrumentationScope(
                            "agentic-customer-service-platform", "PRIVATE_VERSION"
                        ),
                        [metric],
                        "PRIVATE_SCHEMA",
                    )
                ],
                "PRIVATE_SCHEMA",
            )
        ]
    )


class Failure(grpc.RpcError):  # type: ignore[misc]
    def __init__(self, code: grpc.StatusCode, retry: bytes | None = None) -> None:
        self.result = code
        self.retry = retry

    def code(self) -> grpc.StatusCode:
        return self.result

    def details(self) -> str:
        pytest.fail("metric exporter inspected sensitive RPC details")

    def trailing_metadata(self) -> tuple[tuple[str, bytes], ...]:
        return (("google.rpc.retryinfo-bin", self.retry),) if self.retry else ()


class Capture:
    def __init__(self) -> None:
        self.requests: list[bytes] = []
        self.timeouts: list[float] = []
        self.channels = 0
        self.closed = 0
        self.failure: grpc.StatusCode | None = None
        self.retry: bytes | None = None
        self.partial = False
        self.headers: object = None
        self.compression: object = None

    def channel(self, *_args: object, **kwargs: object) -> Capture:
        self.channels += 1
        self.compression = kwargs.get("compression")
        return self

    def close(self) -> None:
        self.closed += 1

    def Export(
        self, request: ExportMetricsServiceRequest, **kwargs: Any
    ) -> ExportMetricsServiceResponse:
        self.requests.append(request.SerializeToString())
        self.timeouts.append(kwargs["timeout"])
        self.headers = kwargs["metadata"]
        if self.failure is not None:
            raise Failure(self.failure, self.retry)
        return ExportMetricsServiceResponse(
            partial_success=ExportMetricsPartialSuccess(
                rejected_data_points=1 if self.partial else 0, error_message="PRIVATE_PARTIAL"
            )
        )


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Capture, PrivacyOTLPMetricExporter]]:
    transport = Capture()
    monkeypatch.setattr(grpc, "insecure_channel", transport.channel)
    monkeypatch.setattr(module, "MetricsServiceStub", lambda _: transport)
    exporter = PrivacyOTLPMetricExporter(
        endpoint="http://localhost:4317", service_name="test", timeout_millis=10000
    )
    try:
        yield transport, exporter
    finally:
        exporter.shutdown()


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
def test_retry_reuses_final_sanitized_request_and_deadline(
    capture: tuple[Capture, PrivacyOTLPMetricExporter],
    monkeypatch: pytest.MonkeyPatch,
    code: grpc.StatusCode,
) -> None:
    transport, exporter = capture
    transport.failure = code
    now = [0.0]
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(random, "uniform", lambda *_: 1)
    waits: list[float] = []

    def wait(delay: float) -> bool:
        waits.append(delay)
        now[0] += delay
        transport.failure = None
        return False

    monkeypatch.setattr(exporter._stopped, "wait", wait)
    assert exporter.export(data(), timeout_millis=2500) == MetricExportResult.SUCCESS
    assert waits == [1.0] and transport.timeouts == [2.5, 1.5]
    assert len(transport.requests) == 2 and transport.requests[0] == transport.requests[1]
    assert b"PRIVATE" not in transport.requests[0]
    assert transport.channels == (2 if code == grpc.StatusCode.UNAVAILABLE else 1)


@pytest.mark.parametrize(
    "code",
    [
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.UNKNOWN,
    ],
)
def test_failure_does_not_log_rpc_details(
    capture: tuple[Capture, PrivacyOTLPMetricExporter],
    code: grpc.StatusCode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport, exporter = capture
    transport.failure = code
    assert exporter.export(data()) == MetricExportResult.FAILURE
    assert len(transport.requests) == 1
    assert "PRIVATE" not in caplog.text and "localhost" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_partial_rejection_is_failure_without_backend_message(
    capture: tuple[Capture, PrivacyOTLPMetricExporter], caplog: pytest.LogCaptureFixture
) -> None:
    transport, exporter = capture
    transport.partial = True
    assert exporter.export(data()) == MetricExportResult.FAILURE
    assert len(transport.requests) == 1 and "PRIVATE" not in caplog.text


def test_retry_info_and_deadline_are_respected(
    capture: tuple[Capture, PrivacyOTLPMetricExporter], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, exporter = capture
    retry = RetryInfo()
    retry.retry_delay.seconds = 100
    transport.retry = retry.SerializeToString()
    transport.failure = grpc.StatusCode.RESOURCE_EXHAUSTED
    assert exporter.export(data(), timeout_millis=50) == MetricExportResult.FAILURE
    assert len(transport.requests) == 1
    assert 0 < transport.timeouts[0] <= 0.05


def test_shutdown_interrupts_retry_and_closes_once(
    capture: tuple[Capture, PrivacyOTLPMetricExporter], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, exporter = capture
    transport.failure = grpc.StatusCode.RESOURCE_EXHAUSTED
    waiting = Event()
    original_wait = exporter._stopped.wait

    def wait(delay: float) -> bool:
        waiting.set()
        return original_wait(delay)

    monkeypatch.setattr(exporter._stopped, "wait", wait)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(exporter.export, data())
        assert waiting.wait(5)
        assert not exporter.force_flush(timeout_millis=0)
        exporter.shutdown()
        assert future.result(5) == MetricExportResult.FAILURE
    exporter.shutdown()
    assert transport.closed == 1 and len(transport.requests) == 1
    assert exporter.force_flush(timeout_millis=100)
    assert exporter.export(data()) == MetricExportResult.FAILURE


def test_headers_compression_tls_and_signal_precedence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    transport = Capture()
    for name in ("CERTIFICATE", "CLIENT_KEY", "CLIENT_CERTIFICATE"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_" + name, str(path))
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=WRONG_SIGNAL")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "authorization=TRACE_ONLY")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_HEADERS", "authorization=Bearer%20PRIVATE_TOKEN")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_COMPRESSION", "gzip")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_INSECURE", "true")
    credentials: dict[str, object] = {}

    def ssl(**kwargs: object) -> object:
        credentials.update(kwargs)
        return "credentials"

    def secure(target: str, creds: object, **kwargs: object) -> Capture:
        assert target == "collector:4317" and creds == "credentials"
        return transport.channel(**kwargs)

    monkeypatch.setattr(grpc, "ssl_channel_credentials", ssl)
    monkeypatch.setattr(grpc, "secure_channel", secure)
    monkeypatch.setattr(grpc, "insecure_channel", lambda *_a, **_k: pytest.fail("https downgraded"))
    monkeypatch.setattr(module, "MetricsServiceStub", lambda _: transport)
    exporter = PrivacyOTLPMetricExporter(endpoint="https://collector:4317", service_name="test")
    try:
        assert exporter.export(data()) == MetricExportResult.SUCCESS
        assert credentials == {
            "root_certificates": b"CERTIFICATE",
            "private_key": b"CLIENT_KEY",
            "certificate_chain": b"CLIENT_CERTIFICATE",
        }
        assert transport.headers == (("authorization", "Bearer PRIVATE_TOKEN"),)
        assert transport.compression == grpc.Compression.Gzip
        assert b"PRIVATE_TOKEN" not in transport.requests[0]
    finally:
        exporter.shutdown()


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "http://collector:4317/path",
        "http://user:PRIVATE@collector:4317",
        "http://collector:4317?token=PRIVATE",
        "ftp://collector:4317",
    ],
)
def test_invalid_endpoint_has_bounded_failure(endpoint: str) -> None:
    with pytest.raises(ValueError, match="^telemetry_invalid_metrics_endpoint$"):
        PrivacyOTLPMetricExporter(endpoint=endpoint, service_name="test")


def test_real_grpc_reader_exporter_delivery_and_shutdown() -> None:
    requests: list[ExportMetricsServiceRequest] = []
    delivered = Event()

    class Receiver(MetricsServiceServicer):
        def Export(
            self, request: ExportMetricsServiceRequest, context: Any
        ) -> ExportMetricsServiceResponse:
            requests.append(request)
            delivered.set()
            return ExportMetricsServiceResponse()

    pool = ThreadPoolExecutor(max_workers=1)
    server = grpc.server(pool)
    add_MetricsServiceServicer_to_server(Receiver(), server)  # type: ignore[no-untyped-call]
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    provider = build_metric_provider(
        Settings.model_construct(
            otel_metrics_enabled=True,
            otel_exporter_otlp_metrics_endpoint=f"http://127.0.0.1:{port}",
            otel_metric_export_interval_millis=300000,
        )
    )
    try:
        provider.activate()
        instruments = build_metrics(provider.get_meter("agentic-customer-service-platform"))
        instruments.agent_runs_total.add(
            2, {"customer.id": "PRIVATE_CUSTOMER", "Authorization": "Bearer PRIVATE"}
        )
        instruments.agent_run_duration_seconds.record(0.25, {"status": "ok"})
        assert provider.force_flush(timeout_millis=5000)
        assert delivered.wait(5)
        metrics = {
            m.name: m
            for scope in requests[0].resource_metrics[0].scope_metrics
            for m in scope.metrics
        }
        assert metrics["agent_runs_total"].sum.data_points[0].as_int == 2
        assert metrics["agent_run_duration_seconds"].histogram.data_points[0].sum == 0.25
        assert b"PRIVATE" not in requests[0].SerializeToString()
    finally:
        provider.shutdown()
        server.stop(0).wait(5)
        pool.shutdown()
    count = len(requests)
    instruments.agent_runs_total.add(10)
    provider.shutdown()
    assert len(requests) == count


def test_retry_exhaustion_is_bounded_to_six_attempts(
    capture: tuple[Capture, PrivacyOTLPMetricExporter], monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, exporter = capture
    transport.failure = grpc.StatusCode.RESOURCE_EXHAUSTED
    exporter._timeout = 1000
    now = [0.0]
    waits: list[float] = []
    monkeypatch.setattr(module, "monotonic", lambda: now[0])
    monkeypatch.setattr(random, "uniform", lambda *_: 1)

    def wait(delay: float) -> bool:
        waits.append(delay)
        now[0] += delay
        return False

    monkeypatch.setattr(exporter._stopped, "wait", wait)
    assert exporter.export(data(), timeout_millis=1000000) == MetricExportResult.FAILURE
    assert len(transport.requests) == 6
    assert waits == [1, 2, 4, 8, 16]
    assert len(set(transport.requests)) == 1
