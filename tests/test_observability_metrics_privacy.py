"""Metric recording cardinality and final public-DTO/protobuf privacy contracts."""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

import pytest
from opentelemetry.proto.metrics.v1.metrics_pb2 import Metric as WireMetric
from opentelemetry.sdk.metrics import AlwaysOffExemplarFilter, Exemplar, MeterProvider
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Histogram,
    HistogramDataPoint,
    InMemoryMetricReader,
    Metric,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope

from app.observability.metric_export import build_metric_request
from app.observability.metric_privacy import APP_SCOPE, CATALOG, labels_for, scope_for
from app.observability.metric_provider import PrivacyMeterProvider
from app.observability.metrics import build_metrics
from app.observability.privacy import register_route_templates, registered_route_templates


@pytest.fixture
def recording() -> Iterator[tuple[PrivacyMeterProvider, InMemoryMetricReader]]:
    routes = registered_route_templates()
    reader = InMemoryMetricReader()
    sdk = MeterProvider(
        metric_readers=[reader], shutdown_on_exit=False, exemplar_filter=AlwaysOffExemplarFilter()
    )
    facade = PrivacyMeterProvider(sdk)
    try:
        yield facade, reader
    finally:
        facade.shutdown()
        register_route_templates(routes)


def dataset(metrics: list[Metric], *, scope: str = APP_SCOPE) -> MetricsData:
    return MetricsData(
        [
            ResourceMetrics(
                Resource(
                    {
                        "host.name": "PRIVATE_HOST",
                        "process.command_line": "PRIVATE_COMMAND",
                        "service.name": "PRIVATE_RESOURCE",
                    }
                ),
                [
                    ScopeMetrics(
                        InstrumentationScope(
                            scope, "PRIVATE_VERSION", attributes={"scope.secret": "PRIVATE_SCOPE"}
                        ),
                        metrics,
                        "PRIVATE_SCHEMA",
                    )
                ],
                "PRIVATE_SCHEMA",
            )
        ]
    )


def counter(name: str, attrs: dict[str, Any], value: int | float = 1) -> Metric:
    point = NumberDataPoint(
        attrs,
        1,
        2,
        value,
        exemplars=[Exemplar({"content": "PRIVATE_EXEMPLAR"}, value, 2, 123, 456)],
    )
    return Metric(
        name,
        "PRIVATE_DESCRIPTION",
        "PRIVATE_UNIT",
        Sum([point], AggregationTemporality.CUMULATIVE, True),
    )


def wire_metrics(data: MetricsData) -> list[WireMetric]:
    request = build_metric_request(data, service_name="test-service")
    return [
        metric
        for resource in request.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]


def test_catalog_matches_all_existing_domain_instruments() -> None:
    tree = ast.parse(Path("app/observability/metrics.py").read_text())
    definitions = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"create_counter", "create_histogram"}
    ]
    assert len(definitions) == 41
    assert len(CATALOG) == 43
    for definition in definitions:
        name = ast.literal_eval(definition.args[0])
        values = {k.arg: ast.literal_eval(k.value) for k in definition.keywords}
        assert CATALOG[name].unit == values["unit"]
        assert CATALOG[name].description == values["description"]
        assert isinstance(definition.func, ast.Attribute)
        assert CATALOG[name].kind == definition.func.attr.removeprefix("create_")


def test_all_domain_instruments_record_through_one_real_sdk(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    instruments = build_metrics(facade.get_meter(APP_SCOPE))
    for name, spec in CATALOG.items():
        if scope_for(name) != APP_SCOPE:
            continue
        instrument = getattr(instruments, name)
        (instrument.record if spec.kind == "histogram" else instrument.add)(1)
    data = reader.get_metrics_data()
    assert data is not None
    exported = wire_metrics(data)
    assert {metric.name for metric in exported} == set(CATALOG) - {
        "http.server.request.duration",
        "http.server.active_requests",
    }
    assert all(metric.unit == CATALOG[metric.name].unit for metric in exported)
    assert len(exported) == 41


def test_unknown_labels_are_bounded_before_sdk_aggregation(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    instrument = facade.get_meter(APP_SCOPE).create_counter(
        "escalations_total", description="PRIVATE_DESCRIPTION"
    )
    for i in range(1000):
        instrument.add(
            1,
            {
                "priority": f"PRIVATE_PRIORITY_{i}",
                "customer.id": str(i),
                "request.id": f"PRIVATE_UUID_{i}",
            },
        )
    data = reader.get_metrics_data()
    assert data is not None
    metric = data.resource_metrics[0].scope_metrics[0].metrics[0]
    assert len(metric.data.data_points) == 1
    assert isinstance(metric.data, Sum)
    point = metric.data.data_points[0]
    assert point.attributes == {"priority": "unknown"}
    assert point.value == 1000
    assert "PRIVATE" not in data.to_json()


def test_dlp_reason_normalization_does_not_change_business_result(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    reason = "dlp_restricted_content:PRIVATE_EMAIL,PRIVATE_CREDENTIAL"
    build_metrics(facade.get_meter(APP_SCOPE)).memory_rejections_total.add(1, {"reason": reason})
    assert reason.endswith("PRIVATE_CREDENTIAL")
    data = reader.get_metrics_data()
    assert data is not None
    wire = wire_metrics(data)[0]
    assert wire.sum.data_points[0].attributes[0].value.string_value == "dlp_restricted_content"
    assert b"PRIVATE" not in wire.SerializeToString()


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 2**63, "PRIVATE_NUMBER"])
def test_invalid_counter_measurements_are_noop(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader], value: Any
) -> None:
    facade, reader = recording
    facade.get_meter(APP_SCOPE).create_counter("agent_runs_total").add(value)
    assert reader.get_metrics_data() is None


def test_unknown_scopes_names_types_and_metadata_never_reach_sdk(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    for i in range(100):
        facade.get_meter(f"PRIVATE_SCOPE_{i}").create_counter("agent_runs_total").add(1)
        facade.get_meter(
            APP_SCOPE, f"PRIVATE_VERSION_{i}", "PRIVATE_SCHEMA", {"identity": "PRIVATE"}
        ).create_counter(f"PRIVATE_NAME_{i}").add(1)
    facade.get_meter(APP_SCOPE).create_histogram("agent_runs_total").record(1)
    facade.get_meter(APP_SCOPE).create_observable_counter(
        "PRIVATE_OBSERVABLE", callbacks=[lambda _: pytest.fail("unapproved callback executed")]
    )
    assert reader.get_metrics_data() is None
    assert len(facade.meters) == 1
    meter = facade.get_meter(APP_SCOPE)
    assert cast(Any, meter).instruments == {}


def test_retained_instruments_stop_after_owned_shutdown(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    instrument = facade.get_meter(APP_SCOPE).create_counter("agent_runs_total")
    instrument.add(1)
    before = reader.get_metrics_data()
    assert before is not None
    facade.shutdown()
    instrument.add(1, {"content": "PRIVATE"})
    assert facade.closed
    assert (
        facade.get_meter(APP_SCOPE).create_counter("agent_runs_total").__class__.__name__
        == "NoOpCounter"
    )


@pytest.mark.parametrize(
    "name,value",
    [
        ("rag_grounding_answer_confidence", 1.1),
        ("rag_grounding_citation_coverage", -0.1),
        ("rag_grounding_retrieval_count", 1.5),
        ("agent_run_duration_seconds", float("inf")),
    ],
)
def test_invalid_histogram_inputs_are_noop(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader], name: str, value: float
) -> None:
    facade, reader = recording
    facade.get_meter(APP_SCOPE).create_histogram(name).record(value)
    assert reader.get_metrics_data() is None


def test_native_instruments_share_facade_but_are_not_enabled_in_fastapi_yet(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    facade, reader = recording
    register_route_templates(["/metrics-test/{item}"])
    meter = facade.get_meter("fastapi", "PRIVATE_VERSION", "PRIVATE_SCHEMA")
    active = meter.create_up_down_counter("http.server.active_requests")
    active.add(1, {"http.request.method": "GET", "url.scheme": "http"})
    active.add(-1, {"http.request.method": "GET", "url.scheme": "http"})
    meter.create_histogram("http.server.request.duration").record(
        0.2,
        {
            "http.route": "/metrics-test/{item}",
            "http.request.method": "GET",
            "http.response.status_code": 200,
        },
    )
    build_metrics(facade.get_meter(APP_SCOPE)).agent_runs_total.add(1)
    data = reader.get_metrics_data()
    assert data is not None
    exported = {m.name: m for m in wire_metrics(data)}
    assert exported["http.server.active_requests"].sum.data_points[0].as_int == 0
    assert not exported["http.server.active_requests"].sum.is_monotonic
    duration = exported["http.server.request.duration"].histogram.data_points[0]
    assert duration.count == 1 and duration.sum == 0.2
    assert sum(duration.bucket_counts) == 1
    assert list(duration.explicit_bounds) == [
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1,
        2.5,
        5,
        7.5,
        10,
    ]
    assert not duration.exemplars


@pytest.mark.parametrize(
    "key",
    [
        "customer.id",
        "actor.id",
        "conversation.id",
        "tenant.id",
        "checkpoint.thread_id",
        "agent.action_id",
        "memory.key",
        "request.id",
        "agent.run_id",
        "url.path",
        "url.query",
        "url.full",
        "server.address",
        "client.address",
        "Authorization",
        "Cookie",
        "Set-Cookie",
        "jwt",
        "credentials",
        "request.body",
        "response.body",
        "prompt",
        "model.output",
        "rag.chunk",
        "memory.content",
        "tool.arguments",
        "sql.parameters",
        "exception.message",
        "exception.stacktrace",
        "exception.cause",
        "status.description",
    ],
)
def test_final_boundary_drops_unsafe_points_not_relabels(key: str) -> None:
    secret = "PRIVATE_SENTINEL bearer eyJhbGci.customer/message?token=secret"
    metrics = [
        counter("agent_runs_total", {key: value})
        for value in (secret, quote(secret), json.dumps({"value": secret}))
    ]
    metrics.append(counter("agent_runs_total", {}, 5))
    request = build_metric_request(dataset(metrics), service_name="test-service")
    serialized = request.SerializeToString()
    assert b"PRIVATE" not in serialized and secret.encode() not in serialized
    metric = request.resource_metrics[0].scope_metrics[0].metrics[0]
    assert len(metric.sum.data_points) == 1 and metric.sum.data_points[0].as_int == 5
    assert not metric.sum.data_points[0].exemplars
    assert not request.resource_metrics[0].schema_url
    assert not request.resource_metrics[0].scope_metrics[0].schema_url
    assert not request.resource_metrics[0].scope_metrics[0].scope.attributes
    resource = {
        a.key: a.value.string_value for a in request.resource_metrics[0].resource.attributes
    }
    assert resource["service.name"] == "test-service"
    assert set(resource) == {
        "service.name",
        "telemetry.sdk.name",
        "telemetry.sdk.language",
        "telemetry.sdk.version",
    }


def test_final_boundary_drops_invalid_allowed_values_unknown_metadata_and_duplicates() -> None:
    data = dataset(
        [
            counter("escalations_total", {"priority": "PRIVATE"}),
            counter("PRIVATE_NAME", {}),
            counter("agent_runs_total", {}, 2),
            counter("agent_runs_total", {}, 3),
            counter("memory_reads_total", {"status": "ok"}),
        ]
    )
    exported = wire_metrics(data)
    assert [m.name for m in exported] == ["memory_reads_total"]
    assert not build_metric_request(
        dataset([counter("agent_runs_total", {})], scope="PRIVATE_SCOPE"), service_name="test"
    ).resource_metrics
    assert not build_metric_request(
        dataset([counter("agent_runs_total", {})], scope="fastapi"), service_name="test"
    ).resource_metrics


@pytest.mark.parametrize(
    "mutation", ["nan", "negative", "delta", "nonmonotonic", "timing", "overflow"]
)
def test_final_sum_semantics_fail_closed(mutation: str) -> None:
    metric = counter("agent_runs_total", {})
    payload = metric.data
    assert isinstance(payload, Sum)
    point = payload.data_points[0]
    if mutation in {"nan", "negative", "overflow"}:
        point = replace(
            point, value={"nan": float("nan"), "negative": -1, "overflow": 2**63}[mutation]
        )
    if mutation == "timing":
        point = replace(point, start_time_unix_nano=3)
    payload = replace(
        payload,
        data_points=[point],
        aggregation_temporality=AggregationTemporality.DELTA
        if mutation == "delta"
        else payload.aggregation_temporality,
        is_monotonic=mutation != "nonmonotonic",
    )
    assert not wire_metrics(dataset([replace(metric, data=payload)]))


def test_final_histogram_preserves_values_and_strips_exemplars() -> None:
    point = HistogramDataPoint(
        {"status": "ok"},
        10,
        20,
        2,
        0.7,
        [1, 1],
        [0.5],
        0.2,
        0.5,
        exemplars=[Exemplar({"prompt": "PRIVATE"}, 0.2, 20, 123, 456)],
    )
    metric = Metric(
        "agent_run_duration_seconds",
        "PRIVATE",
        "PRIVATE",
        Histogram([point], AggregationTemporality.CUMULATIVE),
    )
    wire = wire_metrics(dataset([metric]))[0].histogram
    assert wire.aggregation_temporality == 2
    result = wire.data_points[0]
    assert (
        result.start_time_unix_nano,
        result.time_unix_nano,
        result.count,
        result.sum,
        result.min,
        result.max,
    ) == (10, 20, 2, 0.7, 0.2, 0.5)
    assert list(result.bucket_counts) == [1, 1] and list(result.explicit_bounds) == [0.5]
    assert not result.exemplars and b"PRIVATE" not in wire.SerializeToString()
    assert isinstance(metric.data, Histogram)
    invalid = replace(point, bucket_counts=[1, 2])
    assert not wire_metrics(
        dataset([replace(metric, data=replace(metric.data, data_points=[invalid]))])
    )


def test_route_and_typed_labels_are_not_validated_by_truncation() -> None:
    assert labels_for("http.server.request.duration", {"http.route": "/customer/PRIVATE"}) is None
    assert labels_for("authentication_attempts_total", {"auth_success": "true"}) is None
    assert labels_for("http.server.request.duration", {"http.response.status_code": True}) is None
    assert labels_for("escalations_total", {"priority": "highPRIVATE"}) == {"priority": "unknown"}
    assert labels_for("escalations_total", {"priority": ["high"]}) == {"priority": "unknown"}


def test_sdk_exemplars_are_off_even_with_valid_current_trace(
    recording: tuple[PrivacyMeterProvider, InMemoryMetricReader],
) -> None:
    from opentelemetry.sdk.trace import TracerProvider

    facade, reader = recording
    provider = TracerProvider(shutdown_on_exit=False)
    try:
        with provider.get_tracer("test").start_as_current_span("test") as active:
            assert active.get_span_context().is_valid
            facade.get_meter(APP_SCOPE).create_histogram("agent_run_duration_seconds").record(
                0.1, {"status": "ok", "content": "PRIVATE_EXEMPLAR_CONTENT"}
            )
        data = reader.get_metrics_data()
        assert data is not None
        assert (
            not data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0].exemplars
        )
        assert b"PRIVATE" not in build_metric_request(data, service_name="test").SerializeToString()
    finally:
        provider.shutdown()


def test_projected_resources_do_not_merge_duplicate_cumulative_producers() -> None:
    first = dataset([counter("agent_runs_total", {}, 2)]).resource_metrics[0]
    second = replace(first, resource=Resource({"host.name": "PRIVATE_OTHER_HOST"}))
    assert not build_metric_request(
        MetricsData([first, second]), service_name="test"
    ).resource_metrics


def test_final_ratio_histogram_cannot_exceed_bounded_measurement_range() -> None:
    point = HistogramDataPoint({}, 1, 2, 1, 5.0, [1, 0], [10.0], 5.0, 5.0)
    metric = Metric(
        "rag_grounding_citation_coverage",
        "PRIVATE",
        "PRIVATE",
        Histogram([point], AggregationTemporality.CUMULATIVE),
    )
    assert not wire_metrics(dataset([metric]))


@pytest.mark.parametrize("kind", ["sum", "histogram", "gauge"])
def test_direct_sdk_writes_cannot_bypass_final_otlp_privacy(kind: str) -> None:
    from google.protobuf.json_format import MessageToJson
    from opentelemetry.metrics import Observation
    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
        ExportMetricsServiceRequest,
    )

    reader = InMemoryMetricReader()
    sdk = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)
    meter = sdk.get_meter(
        APP_SCOPE, "PRIVATE_DIRECT_SCOPE_VERSION", attributes={"identity": "PRIVATE_DIRECT_SCOPE"}
    )
    try:
        # Write directly to SDK instruments, without the facade or label helper.
        meter.create_counter("agent_runs_total").add(7)
        secret = "PRIVATE_DIRECT_MESSAGE bearer JWT/customer?id=secret"
        unsafe = {
            "status": "ok",
            "customer.id": secret,
            "prompt": quote(secret),
            "memory.content": json.dumps({"content": secret}),
        }
        if kind == "sum":
            instrument = meter.create_counter("memory_reads_total")
            instrument.add(3, {"status": "ok"})
            instrument.add(5, unsafe)
        elif kind == "histogram":
            histogram = meter.create_histogram("agent_run_duration_seconds")
            histogram.record(0.25, {"status": "ok"})
            histogram.record(0.75, unsafe)
        else:
            meter.create_observable_gauge(
                "memory_reads_total",
                callbacks=[lambda _: [Observation(9, unsafe), Observation(11, {"status": "ok"})]],
            )
        data = reader.get_metrics_data()
        assert data is not None
        raw = [m for r in data.resource_metrics for s in r.scope_metrics for m in s.metrics]
        assert len(raw) == 2
        assert "PRIVATE_DIRECT_MESSAGE" in data.to_json()
        payload = build_metric_request(data, service_name="test-service").SerializeToString()
        final = ExportMetricsServiceRequest.FromString(payload)
        rendered = MessageToJson(final)
        for value in (
            secret,
            quote(secret),
            json.dumps({"content": secret}),
            "PRIVATE_DIRECT_SCOPE",
        ):
            assert value.encode() not in payload
            assert value not in rendered
        exported = {
            m.name: m for r in final.resource_metrics for s in r.scope_metrics for m in s.metrics
        }
        assert exported["agent_runs_total"].sum.data_points[0].as_int == 7
        if kind == "sum":
            points = exported["memory_reads_total"].sum.data_points
            assert len(points) == 1 and points[0].as_int == 3
        elif kind == "histogram":
            points = exported["agent_run_duration_seconds"].histogram.data_points
            assert len(points) == 1 and points[0].count == 1 and points[0].sum == 0.25
        else:
            assert set(exported) == {"agent_runs_total"}
        assert all(m.WhichOneof("data") != "gauge" for m in exported.values())
    finally:
        sdk.shutdown()
