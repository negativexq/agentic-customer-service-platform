"""Native HTTP measurements through the owned facade and final real gRPC payload."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc
import httpx
import pytest
from fastapi import FastAPI
from google.protobuf.json_format import MessageToJson
from opentelemetry import metrics
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import (
    MetricsServiceServicer,
    add_MetricsServiceServicer_to_server,
)
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from otel_http_harness import ACTOR, TOKEN, HTTPHarness
from sqlalchemy.orm import Session
from test_observability_http_guardrails import (
    CONVERSATION,
    MESSAGE,
    chat,
    read_decision,
    request_graph,
)

from app.core.config import Settings
from app.observability import metric_provider
from app.observability.metric_provider import PrivacyMeterProvider
from app.observability.metrics import get_operational_summary
from app.observability.middleware import fastapi_telemetry
from app.observability.tracing import get_meter_provider


class Receiver(MetricsServiceServicer):
    def __init__(self) -> None:
        self.requests: list[ExportMetricsServiceRequest] = []

    def Export(self, request: Any, context: Any) -> ExportMetricsServiceResponse:
        self.requests.append(ExportMetricsServiceRequest.FromString(request.SerializeToString()))
        return ExportMetricsServiceResponse()


@pytest.fixture
def receiver() -> Iterator[tuple[str, Receiver]]:
    capture = Receiver()
    with ThreadPoolExecutor(max_workers=2) as executor:
        server = grpc.server(executor)
        add_MetricsServiceServicer_to_server(capture, server)  # type: ignore[no-untyped-call]
        port = server.add_insecure_port("127.0.0.1:0")
        assert port
        server.start()
        try:
            yield f"http://127.0.0.1:{port}", capture
        finally:
            server.stop(0).wait(5)


def final_metrics(capture: Receiver) -> dict[str, Any]:
    assert capture.requests, "Final serialized OTLP capture must not be empty"
    request = capture.requests[-1]
    assert request.resource_metrics
    result = {
        metric.name: metric
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert result
    return result


def assert_private(capture: Receiver, *extra: str) -> None:
    assert capture.requests
    for request in capture.requests:
        wire = request.SerializeToString()
        text = MessageToJson(request)
        for value in (TOKEN, ACTOR, CONVERSATION, MESSAGE, *extra):
            assert value.encode() not in wire and value not in text
        for resource in request.resource_metrics:
            assert {a.key for a in resource.resource.attributes} == {
                "service.name",
                "telemetry.sdk.name",
                "telemetry.sdk.language",
                "telemetry.sdk.version",
            }
            for scope in resource.scope_metrics:
                assert not scope.scope.attributes and not scope.schema_url
                for metric in scope.metrics:
                    data = metric.histogram if metric.HasField("histogram") else metric.sum
                    assert data.data_points
                    assert all(not point.exemplars for point in data.data_points)


def test_native_domain_share_one_owned_pipeline_and_real_final_payload(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, receiver: tuple[str, Receiver]
) -> None:
    endpoint, capture = receiver
    counts = {"provider": 0, "reader": 0, "exporter": 0}
    for key, symbol in (
        ("provider", "SDKMeterProvider"),
        ("reader", "PeriodicExportingMetricReader"),
        ("exporter", "PrivacyOTLPMetricExporter"),
    ):
        original = getattr(metric_provider, symbol)

        def factory(*args: Any, _key: str = key, _original: Any = original, **kwargs: Any) -> Any:
            counts[_key] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(metric_provider, symbol, factory)

    class ExternalMeter(metrics.NoOpMeterProvider):
        def get_meter(self, name: str, *args: Any, **kwargs: Any) -> Any:
            # The trace SDK resolves a global meter for its own diagnostics.
            # Native/application instruments must use explicit owned injection.
            assert name not in {"fastapi", "agentic-customer-service-platform"}
            return super().get_meter(name, *args, **kwargs)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Application must not register a global meter provider")

    external = ExternalMeter()
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: external)
    monkeypatch.setattr(metrics, "set_meter_provider", forbidden)
    harness = HTTPHarness(monkeypatch, db_session)
    with harness.start([read_decision()], metrics_endpoint=endpoint) as pipeline:
        owned = pipeline.owner.get_meter_provider()
        assert isinstance(owned, PrivacyMeterProvider)
        assert get_meter_provider() is owned
        assert pipeline.runtime is not None
        config = fastapi_telemetry(
            pipeline.runtime.settings, pipeline.owner.get_tracer_provider(), owned
        )
        assert config["metrics"] is True and config["meter_provider"] is owned
        assert config["logs"] is config["auto_configure"] is False
        response = chat(pipeline).json()
        request_graph(pipeline, response["agent_run_id"])
        assert owned.force_flush(5000)
        final = final_metrics(capture)
        assert {
            "agent_runs_total",
            "tool_calls_total",
            "policy_decisions_total",
            "agent_run_duration_seconds",
            "http.server.request.duration",
            "http.server.active_requests",
        } <= final.keys()
        domain = final["agent_runs_total"].sum.data_points
        assert sum(p.as_int for p in domain) == 1
        duration = final["http.server.request.duration"].histogram.data_points
        chat_points = [
            p
            for p in duration
            if any(
                a.key == "http.route" and a.value.string_value == "/agent/chat"
                for a in p.attributes
            )
        ]
        assert len(chat_points) == 1 and chat_points[0].count == 1
        assert chat_points[0].sum >= 0 and sum(chat_points[0].bucket_counts) == 1
        assert final["http.server.active_requests"].sum.is_monotonic is False
        assert all(p.as_int == 0 for p in final["http.server.active_requests"].sum.data_points)
        assert counts == {"provider": 1, "reader": 1, "exporter": 1}
        assert pipeline.factories == {"provider": 1, "exporter": 1}
        assert len(pipeline.processors) == 1
        assert_private(capture)


def assert_active(capture: Receiver, owned: PrivacyMeterProvider, expected: int) -> None:
    assert owned.force_flush(5000)
    points = final_metrics(capture)["http.server.active_requests"].sum.data_points
    assert points
    assert sum(p.as_int for p in points) == expected
    if expected == 0:
        assert all(p.as_int == 0 for p in points)
    else:
        assert sorted(p.as_int for p in points if p.as_int) == [expected]


def test_active_request_overlap_completion_cancellation_and_error_balance(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, receiver: tuple[str, Receiver]
) -> None:
    endpoint, capture = receiver

    async def exercise() -> None:
        entered = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]

        def configure(app: FastAPI) -> None:
            @app.get("/test/metrics-active/{number}")
            async def held(number: int) -> dict[str, bool]:
                entered[number].set()
                await release[number].wait()
                return {"ok": True}

            @app.get("/test/metrics-error")
            async def failure() -> None:
                raise RuntimeError("PRIVATE_ACTIVE_EXCEPTION")

        with HTTPHarness(monkeypatch, db_session).start(
            [], metrics_endpoint=endpoint, configure_app=configure
        ) as pipeline:
            owned = pipeline.owner.get_meter_provider()
            assert isinstance(owned, PrivacyMeterProvider)
            transport = httpx.ASGITransport(app=pipeline.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                pending = [
                    asyncio.create_task(client.get(f"/test/metrics-active/{i}")) for i in range(2)
                ]
                try:
                    for signal in entered[:2]:
                        await asyncio.wait_for(signal.wait(), 5)
                    assert all(not task.done() for task in pending)
                    assert_active(capture, owned, 2)
                    release[0].set()
                    assert (await asyncio.wait_for(pending[0], 5)).status_code == 200
                    assert not pending[1].done()
                    assert_active(capture, owned, 1)
                    release[1].set()
                    assert (await asyncio.wait_for(pending[1], 5)).status_code == 200
                    assert_active(capture, owned, 0)
                    cancelled = asyncio.create_task(client.get("/test/metrics-active/2"))
                    pending.append(cancelled)
                    await asyncio.wait_for(entered[2].wait(), 5)
                    assert_active(capture, owned, 1)
                    cancelled.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(cancelled, 5)
                    assert_active(capture, owned, 0)
                    assert (await client.get("/test/metrics-error")).status_code == 500
                    assert_active(capture, owned, 0)
                    assert_private(capture, "PRIVATE_ACTIVE_EXCEPTION")
                finally:
                    for signal in release:
                        signal.set()
                    for task in pending:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "path,status",
    [
        ("/orders/1", 401),
        ("/agent/chat", 422),
        ("/unregistered/PRIVATE_ROUTE", 404),
        ("/test/metrics-error", 500),
    ],
)
def test_native_error_and_unknown_route_metrics_final_privacy(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    receiver: tuple[str, Receiver],
    path: str,
    status: int,
) -> None:
    endpoint, capture = receiver
    secret = "PRIVATE_T4D_NATIVE_EXCEPTION"

    def configure(app: FastAPI) -> None:
        @app.get("/test/metrics-error")
        def fail() -> None:
            raise RuntimeError(secret)

    with HTTPHarness(monkeypatch, db_session).start(
        [], metrics_endpoint=endpoint, configure_app=configure
    ) as pipeline:
        if path != "/agent/chat":
            pipeline.client.headers.pop("Authorization")
        if path == "/agent/chat":
            response = pipeline.client.post(
                path + "?secret=PRIVATE_QUERY", json={"message": "PRIVATE_BODY"}
            )
        else:
            response = pipeline.client.get(
                path + "?secret=PRIVATE_QUERY", headers={"Cookie": "PRIVATE_COOKIE"}
            )
        assert response.status_code == status
        owned = pipeline.owner.get_meter_provider()
        assert isinstance(owned, PrivacyMeterProvider) and owned.force_flush(5000)
        points = final_metrics(capture)["http.server.request.duration"].histogram.data_points
        assert sum(p.count for p in points) == 1
        assert_active(capture, owned, 0)
        assert any(
            a.key == "http.response.status_code" and a.value.int_value == status
            for p in points
            for a in p.attributes
        )
        assert_private(
            capture, secret, "PRIVATE_ROUTE", "PRIVATE_QUERY", "PRIVATE_BODY", "PRIVATE_COOKIE"
        )


@pytest.mark.parametrize("master,metric_flag", [(False, False), (False, True), (True, False)])
def test_native_metrics_disabled_with_env_and_external_global_meter(
    monkeypatch: pytest.MonkeyPatch,
    db_session: Session,
    receiver: tuple[str, Receiver],
    master: bool,
    metric_flag: bool,
) -> None:
    from opentelemetry.sdk.metrics import MeterProvider

    endpoint, capture = receiver
    reader = InMemoryMetricReader()
    external = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: external)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", endpoint)
    before = get_operational_summary().request_count
    try:
        with HTTPHarness(monkeypatch, db_session).start(
            [read_decision()],
            enabled=master,
            metrics_endpoint=endpoint if metric_flag else None,
            global_meter_provider=external,
        ) as pipeline:
            assert pipeline.client.get("/health").status_code == 200
            response = chat(pipeline)
            assert response.status_code == 200
            assert get_operational_summary().request_count == before + 1
            assert pipeline.projections.get_by_run_id(response.json()["agent_run_id"]) is not None
            assert capture.requests == []
            assert isinstance(pipeline.owner.get_meter_provider(), metrics.NoOpMeterProvider)
            external.get_meter("external").create_counter("external_control").add(1)
            data = reader.get_metrics_data()
            assert data is not None and data.resource_metrics
            assert all(
                not m.name.startswith("http.server.")
                for r in data.resource_metrics
                for s in r.scope_metrics
                for m in s.metrics
            )
        external.get_meter("external").create_counter("post_cleanup_control").add(1)
        assert reader.get_metrics_data() is not None
    finally:
        external.shutdown()


def test_native_route_identifiers_do_not_create_dynamic_label_series(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, receiver: tuple[str, Receiver]
) -> None:
    endpoint, capture = receiver
    with HTTPHarness(monkeypatch, db_session).start([], metrics_endpoint=endpoint) as pipeline:
        for number in range(20):
            response = pipeline.client.get(f"/unregistered/PRIVATE_ROUTE_{number}?q=PRIVATE_QUERY")
            assert response.status_code == 404
        response = pipeline.client.get("/orders/1?customer_id=1&q=PRIVATE_QUERY")
        assert response.status_code == 200
        owned = pipeline.owner.get_meter_provider()
        assert isinstance(owned, PrivacyMeterProvider) and owned.force_flush(5000)
        points = final_metrics(capture)["http.server.request.duration"].histogram.data_points
        assert len(points) == 2 and sum(point.count for point in points) == 21
        routes = {
            a.value.string_value
            for point in points
            for a in point.attributes
            if a.key == "http.route"
        }
        assert routes == {"/orders/{order_id}"}
        assert_private(capture, "PRIVATE_ROUTE", "PRIVATE_QUERY")


@pytest.mark.parametrize("state", ["noop", "external", "inactive", "closed"])
def test_native_metric_configuration_requires_successful_owned_initialization(state: str) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.trace import NoOpTracerProvider

    sdk = MeterProvider(shutdown_on_exit=False)
    supplied: metrics.MeterProvider
    if state == "noop":
        supplied = metrics.NoOpMeterProvider()
    elif state == "external":
        supplied = sdk
    else:
        supplied = PrivacyMeterProvider(sdk, recording_enabled=state == "closed")
        if state == "closed":
            supplied.stop_recording()
    try:
        config = fastapi_telemetry(
            Settings(otel_enabled=True, otel_metrics_enabled=True),
            NoOpTracerProvider(),
            supplied,
        )
        assert config["metrics"] is False
        assert config.get("meter_provider") is None
    finally:
        sdk.shutdown()
