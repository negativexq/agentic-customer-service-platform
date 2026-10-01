"""Real local TLS/mTLS/auth OTLP transport, with final protobuf privacy checks.

Ephemeral certificates and local receivers are portfolio transport evidence,
not deployment TLS acceptance. No SDK globals or exporter channels are mocked.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import grpc
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from google.protobuf.json_format import MessageToJson
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
    MetricsServiceServicer,
    add_MetricsServiceServicer_to_server,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
    TraceServiceServicer,
    add_TraceServiceServicer_to_server,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_observability_metrics_transport import data

from app.observability.export import PrivacyOTLPSpanExporter
from app.observability.metric_export import PrivacyOTLPMetricExporter

TOKEN = "PRIVATE_TLS_BEARER"


@pytest.fixture(autouse=True)
def clear_transport_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in os.environ:
        if name.startswith("OTEL_EXPORTER_OTLP_"):
            monkeypatch.delenv(name)


def certificate(
    name: str,
    *,
    authority: tuple[rsa.RSAPrivateKey, x509.Certificate] | None = None,
    client: bool = False,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(authority[1].subject if authority else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=authority is None, path_length=None), critical=True)
    )
    if authority is not None:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(
                [
                    ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH,
                ]
            ),
            critical=False,
        )
        if not client:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            )
    return key, builder.sign(authority[0] if authority else key, hashes.SHA256())


def pem(pair: tuple[rsa.RSAPrivateKey, x509.Certificate]) -> tuple[bytes, bytes]:
    key, cert = pair
    return (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        cert.public_bytes(serialization.Encoding.PEM),
    )


@pytest.fixture(scope="module")
def certificates() -> dict[str, tuple[bytes, bytes]]:
    root = certificate("T5 Local CA")
    return {
        "ca": pem(root),
        "other": pem(certificate("T5 Other CA")),
        "server": pem(certificate("localhost", authority=root)),
        "client": pem(certificate("T5 Local Client", authority=root, client=True)),
    }


@contextmanager
def tls_files(parent: Path) -> Iterator[Path]:
    """Remove generated transport material even when setup or assertions fail."""
    directory = TemporaryDirectory(prefix="t5-tls-", dir=parent)
    location = Path(directory.name)
    try:
        yield location
    finally:
        directory.cleanup()
        assert not location.exists(), "TLS fixture directory was not removed"


@pytest.mark.parametrize("failure", ["none", "setup", "assertion"])
def test_tls_files_removed_on_success_and_failure(tmp_path: Path, failure: str) -> None:
    location: Path | None = None
    try:
        with tls_files(tmp_path) as location:
            (location / "ca.pem").write_bytes(b"test-only CA")
            if failure == "setup":
                raise ValueError("controlled fixture setup failure")
            (location / "client.key").write_bytes(b"test-only key")
            (location / "client.pem").write_bytes(b"test-only certificate")
            if failure == "assertion":
                raise AssertionError("controlled assertion failure")
    except (ValueError, AssertionError) as error:
        assert failure != "none"
        expected = ValueError if failure == "setup" else AssertionError
        assert isinstance(error, expected)
    else:
        assert failure == "none"
    assert location is not None and not location.exists()
    assert list(tmp_path.iterdir()) == []


class TraceReceiver(TraceServiceServicer):
    def __init__(self) -> None:
        self.requests: list[ExportTraceServiceRequest] = []
        self.calls = 0

    def Export(self, request: Any, context: Any) -> ExportTraceServiceResponse:
        self.calls += 1
        if dict(context.invocation_metadata()).get("authorization") != "Bearer " + TOKEN:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "PRIVATE_AUTH_REJECTION")
        self.requests.append(ExportTraceServiceRequest.FromString(request.SerializeToString()))
        return ExportTraceServiceResponse()


class MetricReceiver(MetricsServiceServicer):
    def __init__(self) -> None:
        self.requests: list[ExportMetricsServiceRequest] = []
        self.calls = 0

    def Export(self, request: Any, context: Any) -> ExportMetricsServiceResponse:
        self.calls += 1
        if dict(context.invocation_metadata()).get("authorization") != "Bearer " + TOKEN:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "PRIVATE_AUTH_REJECTION")
        self.requests.append(ExportMetricsServiceRequest.FromString(request.SerializeToString()))
        return ExportMetricsServiceResponse()


@pytest.mark.parametrize("signal", ["traces", "metrics"])
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "wrong-ca",
        "wrong-hostname",
        "missing-auth",
        "wrong-auth",
        "mtls",
        "missing-client",
        "unavailable",
    ],
)
def test_real_tls_transport_and_final_privacy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    certificates: dict[str, tuple[bytes, bytes]],
    caplog: pytest.LogCaptureFixture,
    signal: str,
    case: str,
) -> None:
    root = certificates["ca"][1]
    key, cert = certificates["server"]
    credentials = grpc.ssl_server_credentials(
        [(key, cert)],
        root_certificates=root,
        require_client_auth=case in {"mtls", "missing-client"},
    )
    receiver: TraceReceiver | MetricReceiver = (
        TraceReceiver() if signal == "traces" else MetricReceiver()
    )
    with tls_files(tmp_path) as secret_dir, ThreadPoolExecutor(max_workers=2) as executor:
        server = grpc.server(executor)
        try:
            if isinstance(receiver, TraceReceiver):
                add_TraceServiceServicer_to_server(receiver, server)  # type: ignore[no-untyped-call]
            else:
                add_MetricsServiceServicer_to_server(receiver, server)  # type: ignore[no-untyped-call]
            port = server.add_secure_port("127.0.0.1:0", credentials)
            assert port
            server.start()
            if case == "unavailable":
                server.stop(0).wait(5)
            prefix = "OTEL_EXPORTER_OTLP_" + signal.upper() + "_"
            trust = secret_dir / "ca.pem"
            trust.write_bytes(certificates["other"][1] if case == "wrong-ca" else root)
            monkeypatch.setenv(prefix + "CERTIFICATE", str(trust))
            monkeypatch.setenv(prefix + "TIMEOUT", "1")
            if case != "missing-auth":
                monkeypatch.setenv(
                    prefix + "HEADERS",
                    "authorization=Bearer%20"
                    + ("PRIVATE_WRONG_TOKEN" if case == "wrong-auth" else TOKEN),
                )
            if case == "mtls":
                client_key, client_cert = certificates["client"]
                key_path, cert_path = secret_dir / "client.key", secret_dir / "client.pem"
                key_path.write_bytes(client_key)
                key_path.chmod(0o600)
                cert_path.write_bytes(client_cert)
                monkeypatch.setenv(prefix + "CLIENT_KEY", str(key_path))
                monkeypatch.setenv(prefix + "CLIENT_CERTIFICATE", str(cert_path))
            host = "127.0.0.1" if case == "wrong-hostname" else "localhost"
            success = case in {"valid", "mtls"}
            if signal == "traces":
                exporter = PrivacyOTLPSpanExporter(
                    endpoint=f"https://{host}:{port}", service_name="t5-tls"
                )
                capture = InMemorySpanExporter()
                provider = TracerProvider(shutdown_on_exit=False)
                provider.add_span_processor(SimpleSpanProcessor(capture))
                try:
                    with provider.get_tracer(
                        "agentic-customer-service-platform"
                    ).start_as_current_span("agent.run") as span:
                        span.set_attribute("prompt", "PRIVATE_TLS_PROMPT")
                        span.set_attribute("Authorization", "Bearer " + TOKEN)
                    started = time.monotonic()
                    result = exporter.export(capture.get_finished_spans())
                    assert time.monotonic() - started < 3
                    assert (result == SpanExportResult.SUCCESS) is success
                    assert exporter.force_flush(1000)
                finally:
                    provider.shutdown()
                    exporter.shutdown()
                    exporter.shutdown()
                    assert exporter.export(capture.get_finished_spans()) == SpanExportResult.FAILURE
            else:
                metric_exporter = PrivacyOTLPMetricExporter(
                    endpoint=f"https://{host}:{port}", service_name="t5-tls", timeout_millis=1000
                )
                try:
                    from opentelemetry.sdk.metrics.export import MetricExportResult

                    started = time.monotonic()
                    result_metric = metric_exporter.export(data(), timeout_millis=1000)
                    assert time.monotonic() - started < 3
                    assert (result_metric == MetricExportResult.SUCCESS) is success
                    assert metric_exporter.force_flush(1000)
                finally:
                    metric_exporter.shutdown()
                    metric_exporter.shutdown()
                    assert metric_exporter.export(data()) == MetricExportResult.FAILURE
            assert len(receiver.requests) == (1 if success else 0)
            if success:
                request = receiver.requests[0]
                assert request.SerializeToString()
                assert "PRIVATE" not in MessageToJson(request)
                assert b"PRIVATE" not in request.SerializeToString()
                if isinstance(receiver, MetricReceiver):
                    metric = receiver.requests[0].resource_metrics[0].scope_metrics[0].metrics[0]
                    assert (
                        metric.name == "agent_runs_total" and metric.sum.data_points[0].as_int == 3
                    )
                else:
                    span = receiver.requests[0].resource_spans[0].scope_spans[0].spans[0]
                    assert (
                        span.name == "agent.run"
                        and len(span.trace_id) == 16
                        and len(span.span_id) == 8
                    )
            elif case in {"missing-auth", "wrong-auth"}:
                assert receiver.calls == 1
            else:
                assert receiver.calls == 0
            assert "PRIVATE" not in caplog.text
            assert not any(record.exc_info for record in caplog.records)
        finally:
            server.stop(0).wait(5)
