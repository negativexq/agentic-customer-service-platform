"""Final metric privacy projection and application-owned OTLP gRPC transport."""

from __future__ import annotations

import logging
import math
import os
import random
import re
from collections import Counter as Counts
from collections.abc import Mapping
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from typing import TypedDict
from urllib.parse import unquote, urlparse

import grpc
from google.rpc.error_details_pb2 import RetryInfo
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
    ExportMetricsServiceResponse,
)
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2_grpc import MetricsServiceStub
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, InstrumentationScope, KeyValue
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    AGGREGATION_TEMPORALITY_CUMULATIVE,
    ResourceMetrics,
    ScopeMetrics,
)
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    HistogramDataPoint as WireHistogramPoint,
)
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    Metric as WireMetric,
)
from opentelemetry.proto.metrics.v1.metrics_pb2 import (
    NumberDataPoint as WireNumberPoint,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Histogram,
    HistogramDataPoint,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
    Sum,
)

from app.observability.metric_privacy import CATALOG, SCOPES, labels_for, scope_for, valid_number

logger = logging.getLogger(__name__)
_SDK_VERSION = version("opentelemetry-sdk")
_SCOPE_VERSIONS = {"agentic-customer-service-platform": "", "fastapi": version("fastapi")}
_RETRYABLE_CODES = frozenset(
    {
        grpc.StatusCode.CANCELLED,
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.RESOURCE_EXHAUSTED,
        grpc.StatusCode.ABORTED,
        grpc.StatusCode.OUT_OF_RANGE,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DATA_LOSS,
    }
)


class _PointFields(TypedDict):
    attributes: list[KeyValue]
    start_time_unix_nano: int
    time_unix_nano: int


def _attributes(values: Mapping[str, str | bool | int]) -> list[KeyValue]:
    result = []
    for key, value in values.items():
        encoded = AnyValue()
        if type(value) is bool:
            encoded.bool_value = value
        elif type(value) is int:
            encoded.int_value = value
        else:
            assert isinstance(value, str)
            encoded.string_value = value
        result.append(KeyValue(key=key, value=encoded))
    return result


def _uint(value: object) -> bool:
    return type(value) is int and 0 <= value < 2**64


def build_metric_request(data: MetricsData, *, service_name: str) -> ExportMetricsServiceRequest:
    """Project public point fields; never serialize original metadata/exemplars."""
    metrics: dict[tuple[str, str], WireMetric] = {}
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            scope = scope_metrics.scope.name
            if scope not in SCOPES:
                continue
            for metric in scope_metrics.metrics:
                spec = CATALOG.get(metric.name)
                if spec is None or scope_for(metric.name) != scope:
                    continue
                payload = metric.data
                if (
                    not isinstance(payload, (Sum, Histogram))
                    or payload.aggregation_temporality != AggregationTemporality.CUMULATIVE
                ):
                    continue
                if isinstance(payload, Sum):
                    if spec.kind not in {"counter", "up_down_counter"} or payload.is_monotonic != (
                        spec.kind == "counter"
                    ):
                        continue
                elif spec.kind != "histogram":
                    continue
                key = (scope, metric.name)
                wire = metrics.setdefault(
                    key, WireMetric(name=metric.name, unit=spec.unit, description=spec.description)
                )
                for point in payload.data_points:
                    labels = labels_for(metric.name, point.attributes, strict=True)
                    if (
                        labels is None
                        or not _uint(point.start_time_unix_nano)
                        or not _uint(point.time_unix_nano)
                        or point.start_time_unix_nano > point.time_unix_nano
                    ):
                        continue
                    fields: _PointFields = dict(
                        attributes=_attributes(labels),
                        start_time_unix_nano=point.start_time_unix_nano,
                        time_unix_nano=point.time_unix_nano,
                    )
                    if isinstance(payload, Sum):
                        if not isinstance(point, NumberDataPoint):
                            continue
                        if not valid_number(point.value) or (
                            payload.is_monotonic and point.value < 0
                        ):
                            continue
                        number = WireNumberPoint(**fields)
                        if type(point.value) is int:
                            number.as_int = point.value
                        else:
                            number.as_double = point.value
                        wire.sum.aggregation_temporality = AGGREGATION_TEMPORALITY_CUMULATIVE
                        wire.sum.is_monotonic = payload.is_monotonic
                        wire.sum.data_points.append(number)
                    else:
                        if not isinstance(point, HistogramDataPoint):
                            continue
                        if not _uint(point.count) or not valid_number(point.sum) or point.sum < 0:
                            continue
                        bounds = point.explicit_bounds
                        buckets = point.bucket_counts
                        if (
                            len(buckets) != len(bounds) + 1
                            or not all(_uint(n) for n in buckets)
                            or sum(buckets) != point.count
                        ):
                            continue
                        if not all(valid_number(n) and n >= 0 for n in bounds) or any(
                            a >= b for a, b in zip(bounds, bounds[1:], strict=False)
                        ):
                            continue
                        if any(
                            n is not None and (not valid_number(n) or n < 0)
                            for n in (point.min, point.max)
                        ):
                            continue
                        if (
                            point.min is not None
                            and point.max is not None
                            and point.min > point.max
                        ):
                            continue
                        if metric.name in {
                            "rag_grounding_citation_coverage",
                            "rag_grounding_answer_confidence",
                        } and (
                            point.sum > point.count
                            or any(n is not None and n > 1 for n in (point.min, point.max))
                        ):
                            continue
                        histogram = WireHistogramPoint(
                            **fields,
                            count=point.count,
                            sum=point.sum,
                            bucket_counts=buckets,
                            explicit_bounds=bounds,
                        )
                        if point.min is not None:
                            histogram.min = point.min
                        if point.max is not None:
                            histogram.max = point.max
                        wire.histogram.aggregation_temporality = AGGREGATION_TEMPORALITY_CUMULATIVE
                        wire.histogram.data_points.append(histogram)
    scopes: dict[str, ScopeMetrics] = {}
    for (scope, _), wire in metrics.items():
        points = wire.sum.data_points if wire.HasField("sum") else wire.histogram.data_points
        # Resource projection must not silently merge independently aggregated producers.
        identities = [
            tuple(sorted((attr.key, attr.value.SerializeToString()) for attr in point.attributes))
            for point in points
        ]
        counts = Counts(identities)
        retained = [
            point
            for point, identity in zip(points, identities, strict=True)
            if counts[identity] == 1
        ]
        del points[:]
        points.extend(retained)
        if not points:
            continue
        if scope not in scopes:
            scopes[scope] = ScopeMetrics(
                scope=InstrumentationScope(name=scope, version=_SCOPE_VERSIONS[scope])
            )
        scopes[scope].metrics.append(wire)
    if not scopes:
        return ExportMetricsServiceRequest()
    resource = {
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": _SDK_VERSION,
    }
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", service_name):
        resource["service.name"] = service_name
    return ExportMetricsServiceRequest(
        resource_metrics=[
            ResourceMetrics(
                resource=Resource(attributes=_attributes(resource)),
                scope_metrics=list(scopes.values()),
            )
        ]
    )


def _setting(suffix: str, default: str = "") -> str:
    return os.environ.get(
        "OTEL_EXPORTER_OTLP_METRICS_" + suffix,
        os.environ.get("OTEL_EXPORTER_OTLP_" + suffix, default),
    )


def validate_metric_endpoint(endpoint: str) -> None:
    """Reject malformed destinations without echoing config values."""
    try:
        parsed = urlparse(endpoint)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.path in {"", "/"}
            and not (parsed.query or parsed.fragment or parsed.username or parsed.password)
            and (port is None or 1 <= port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("telemetry_invalid_metrics_endpoint")


class PrivacyOTLPMetricExporter(MetricExporter):
    """Encode only approved data, then transmit it using public protobuf/gRPC APIs.

    PeriodicExportingMetricReader serializes export calls. Shutdown may interrupt retries;
    flush waits for an active call and does not claim successful delivery.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        service_name: str,
        timeout_millis: float = 5000,
    ) -> None:
        super().__init__()
        validate_metric_endpoint(endpoint)
        parsed = urlparse(endpoint)
        self._target = parsed.netloc or endpoint
        self._insecure = (
            parsed.scheme != "https"
            and _setting("INSECURE", "true" if parsed.scheme == "http" else "false").lower()
            == "true"
        )
        self._timeout = timeout_millis / 1000
        if not math.isfinite(self._timeout) or self._timeout <= 0:
            raise ValueError("telemetry_invalid_timeout")
        compression = _setting("COMPRESSION", "none").lower()
        self._compression = {
            "none": grpc.Compression.NoCompression,
            "gzip": grpc.Compression.Gzip,
            "deflate": grpc.Compression.Deflate,
        }[compression]
        self._headers = tuple(
            (key.strip(), unquote(value.strip()))
            for item in _setting("HEADERS").split(",")
            if item.strip()
            for key, value in [item.split("=", 1)]
        )
        self._credentials = None
        if not self._insecure:

            def read(suffix: str) -> bytes | None:
                path = _setting(suffix)
                return Path(path).read_bytes() if path else None

            self._credentials = grpc.ssl_channel_credentials(
                root_certificates=read("CERTIFICATE"),
                private_key=read("CLIENT_KEY"),
                certificate_chain=read("CLIENT_CERTIFICATE"),
            )
        self._service_name = service_name
        self._stopped = Event()
        self._export_lock = Lock()
        self._channel_lock = Lock()
        self._channel = self._open_channel()
        try:
            self._client = MetricsServiceStub(self._channel)  # type: ignore[no-untyped-call]
        except Exception:
            self._channel.close()
            raise

    def _open_channel(self) -> grpc.Channel:
        options = (("grpc.primary_user_agent", f"OTel-OTLP-Exporter-Python/{_SDK_VERSION}"),)
        if self._insecure:
            return grpc.insecure_channel(
                self._target, compression=self._compression, options=options
            )
        assert self._credentials is not None
        return grpc.secure_channel(
            self._target, self._credentials, compression=self._compression, options=options
        )

    def export(
        self, metrics_data: MetricsData, timeout_millis: float = 10000, **kwargs: object
    ) -> MetricExportResult:
        with self._export_lock:
            if self._stopped.is_set():
                return MetricExportResult.FAILURE
            try:
                request = build_metric_request(metrics_data, service_name=self._service_name)
                if not request.resource_metrics:
                    return MetricExportResult.SUCCESS
                deadline = monotonic() + min(self._timeout, max(timeout_millis, 0) / 1000)
                for attempt in range(6):
                    remaining = deadline - monotonic()
                    if remaining <= 0 or self._stopped.is_set():
                        break
                    try:
                        response: ExportMetricsServiceResponse = self._client.Export(
                            request, metadata=self._headers, timeout=remaining
                        )
                        if response.partial_success.rejected_data_points:
                            logger.warning(
                                "Telemetry export partially rejected.",
                                extra={"telemetry_status": "partial_rejection"},
                            )
                            return MetricExportResult.FAILURE
                        return MetricExportResult.SUCCESS
                    except grpc.RpcError as error:
                        delay = (2**attempt) * random.uniform(0.8, 1.2)
                        for key, value in error.trailing_metadata() or ():
                            if key == "google.rpc.retryinfo-bin" and isinstance(value, bytes):
                                retry = RetryInfo.FromString(value)
                                delay = retry.retry_delay.seconds + retry.retry_delay.nanos / 1e9
                        if error.code() not in _RETRYABLE_CODES or attempt == 5:
                            break
                        if error.code() == grpc.StatusCode.UNAVAILABLE and attempt == 0:
                            with self._channel_lock:
                                if self._stopped.is_set():
                                    break
                                self._channel.close()
                                self._channel = self._open_channel()
                                self._client = MetricsServiceStub(self._channel)  # type: ignore[no-untyped-call]
                        if not math.isfinite(delay) or delay < 0 or delay >= deadline - monotonic():
                            break
                        if self._stopped.wait(delay):
                            break
            except Exception:
                pass  # Fail closed, including encoding/config/transport errors; never log raw data.
            logger.warning("Telemetry export failed.", extra={"telemetry_status": "export_failed"})
            return MetricExportResult.FAILURE

    def force_flush(self, timeout_millis: float = 30000) -> bool:
        acquired = self._export_lock.acquire(timeout=max(timeout_millis, 0) / 1000)
        if acquired:
            self._export_lock.release()
        return acquired

    def shutdown(self, timeout_millis: float = 30000, **kwargs: object) -> None:
        with self._channel_lock:
            if not self._stopped.is_set():
                self._stopped.set()
                self._channel.close()
