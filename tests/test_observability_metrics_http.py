"""Enabled domain pipeline tolerates a real unavailable metrics endpoint."""

from __future__ import annotations

import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import grpc
import pytest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceStub
from opentelemetry.sdk.metrics.export import MetricExportResult
from otel_http_harness import ACTOR, TOKEN, HTTPHarness
from sqlalchemy.orm import Session
from test_observability_http_guardrails import (
    CONVERSATION,
    MESSAGE,
    chat,
    read_decision,
    request_graph,
)

from app.observability import metric_export, metric_provider
from app.observability.metric_export import PrivacyOTLPMetricExporter
from app.observability.metric_provider import PrivacyMeterProvider
from app.observability.metrics import get_operational_summary


def test_enabled_domain_metrics_unavailable_endpoint_does_not_block_http_or_traces(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    # Reserve a local port without listening: there is no accepting service and
    # another process cannot occupy the port during this test.
    unavailable = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unavailable.bind(("127.0.0.1", 0))
    address = f"127.0.0.1:{unavailable.getsockname()[1]}"
    endpoint = "http://" + address
    failed_rpc = Event()
    release_failure = Event()
    gate_timed_out = Event()
    attempts: list[ExportMetricsServiceRequest] = []
    rpc_threads: list[int] = []
    rpc_codes: list[grpc.StatusCode] = []
    results: list[MetricExportResult] = []
    exporters: list[PrivacyOTLPMetricExporter] = []
    original = PrivacyOTLPMetricExporter
    original_stub = MetricsServiceStub
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS", "authorization=Bearer%20PRIVATE_ENDPOINT_CREDENTIAL"
    )

    def observed_stub(channel: grpc.Channel) -> Any:
        client = original_stub(channel)  # type: ignore[no-untyped-call]
        real_rpc = client.Export

        def observe_rpc(request: ExportMetricsServiceRequest, **rpc_kwargs: Any) -> Any:
            attempts.append(ExportMetricsServiceRequest.FromString(request.SerializeToString()))
            rpc_threads.append(threading.get_ident())
            try:
                return real_rpc(request, **rpc_kwargs)
            except grpc.RpcError as error:
                rpc_codes.append(error.code())
                if not failed_rpc.is_set():
                    failed_rpc.set()
                    if not release_failure.wait(10):
                        gate_timed_out.set()
                raise

        monkeypatch.setattr(client, "Export", observe_rpc)
        return client

    # UNAVAILABLE recreates the channel and stub. Observe every real stub,
    # including reconnections, rather than retaining only the first RPC capture.
    monkeypatch.setattr(metric_export, "MetricsServiceStub", observed_stub)

    def factory(**kwargs: Any) -> PrivacyOTLPMetricExporter:
        instance = original(**kwargs)
        exporters.append(instance)
        real_export = instance.export

        def observe_export(*args: Any, **export_kwargs: Any) -> MetricExportResult:
            result = real_export(*args, **export_kwargs)
            results.append(result)
            return result

        monkeypatch.setattr(instance, "export", observe_export)
        return instance

    monkeypatch.setattr(metric_provider, "PrivacyOTLPMetricExporter", factory)
    before = get_operational_summary()
    try:
        harness = HTTPHarness(monkeypatch, db_session)
        with harness.start(
            [read_decision(), read_decision()],
            metrics_endpoint=endpoint,
            metrics_timeout_millis=250,
        ) as pipeline:
            owned = pipeline.owner.get_meter_provider()
            assert isinstance(owned, PrivacyMeterProvider)
            assert owned.recording_enabled and len(exporters) == 1
            assert pipeline.runtime is not None and pipeline.runtime.settings.otel_metrics_enabled
            first = chat(pipeline).json()
            request_graph(pipeline, first["agent_run_id"])
            with ThreadPoolExecutor(max_workers=2) as executor:
                flush = executor.submit(owned.force_flush, 5000)
                try:
                    assert failed_rpc.wait(5), "real unavailable gRPC endpoint was not exercised"
                    assert not flush.done()
                    # Request must complete while export holds its lock and has
                    # not yet returned failure. No sleeps or elapsed-time guess.
                    request = executor.submit(
                        chat, pipeline, conversation="PRIVATE_SECOND_CONVERSATION"
                    )
                    response = request.result(timeout=5)
                    assert response.status_code == 200
                    second = response.json()
                    assert second["tool_call"] == {"status": "executed"}
                    assert not flush.done() and not release_failure.is_set()
                    server, spans = request_graph(pipeline, second["agent_run_id"])
                    assert {"agent.run", "tool.execute", "policy.evaluate"} <= {
                        s.name for s in spans
                    }
                    view = pipeline.projections.get_by_run_id(second["agent_run_id"])
                    assert view is not None and view.trace_id == f"{server.context.trace_id:032x}"
                    assert get_operational_summary().request_count == before.request_count + 2
                    assert pipeline.client.get("/health").status_code == 200
                    assert pipeline.client.get("/ready").status_code == 200
                finally:
                    release_failure.set()
                # True is SDK flush completion, not a delivery guarantee.
                assert flush.result(timeout=5)
            assert results == [MetricExportResult.FAILURE]
            assert not gate_timed_out.is_set()
            assert owned.force_flush(5000)
            assert all(r == MetricExportResult.FAILURE for r in results)
            final = {
                m.name: m
                for r in attempts[-1].resource_metrics
                for s in r.scope_metrics
                for m in s.metrics
            }
            assert final["agent_runs_total"].sum.data_points[0].as_int == 2
            assert final["agent_run_duration_seconds"].histogram.data_points[0].count == 2
            assert "tool_calls_total" in final and "policy_decisions_total" in final
            assert not any(name.startswith("http.server.") for name in final)
            assert set(rpc_threads).isdisjoint(pipeline.worker_threads + pipeline.loop_threads)
            pipeline.assert_private_absent(MESSAGE, CONVERSATION, "PRIVATE_SECOND_CONVERSATION")
        assert owned.closed
        pipeline.owner.shutdown()
        owned.shutdown()
        # A refused endpoint can report UNAVAILABLE or exhaust its deadline;
        # channel closure may cancel the last shutdown collection.
        assert rpc_codes and rpc_codes[0] in {
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
        }
        assert set(rpc_codes) <= {
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.DEADLINE_EXCEEDED,
            grpc.StatusCode.CANCELLED,
        }
        assert all(result == MetricExportResult.FAILURE for result in results)
        for request in attempts:
            payload = request.SerializeToString()
            for value in (
                TOKEN,
                ACTOR,
                MESSAGE,
                CONVERSATION,
                "PRIVATE_SECOND_CONVERSATION",
                "PRIVATE_ENDPOINT_CREDENTIAL",
                endpoint,
                address,
            ):
                assert value.encode() not in payload
        for value in (
            TOKEN,
            ACTOR,
            MESSAGE,
            CONVERSATION,
            "PRIVATE_SECOND_CONVERSATION",
            "PRIVATE_ENDPOINT_CREDENTIAL",
            endpoint,
            address,
        ):
            assert value not in caplog.text
        assert "Telemetry export failed." in caplog.text
        assert all(record.exc_info is None for record in caplog.records)
    finally:
        release_failure.set()
        unavailable.close()
