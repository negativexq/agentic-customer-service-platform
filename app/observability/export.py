"""Privacy-safe OTLP encoding and application-owned public gRPC transport."""

from __future__ import annotations

import logging
import math
import os
import random
import re
from collections.abc import Callable, Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from threading import Event, Lock
from time import monotonic
from urllib.parse import unquote, urlparse

import grpc
from google.rpc.error_details_pb2 import RetryInfo
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import TraceServiceStub
from opentelemetry.proto.common.v1.common_pb2 import (
    AnyValue,
    ArrayValue,
    InstrumentationScope,
    KeyValue,
)
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanContext, SpanKind

from app.observability.privacy import (
    DOMAIN_SPAN_NAMES,
    EVENT_NAMES,
    HTTP_METHODS,
    filter_attributes,
    registered_route_templates,
)

logger = logging.getLogger(__name__)
_SDK_VERSION = version("opentelemetry-sdk")
_SCOPE_VERSIONS = {
    "agentic-customer-service-platform": "",
    "opentelemetry.instrumentation.fastapi": version("opentelemetry-instrumentation-fastapi"),
    "opentelemetry.instrumentation.asgi": version("opentelemetry-instrumentation-asgi"),
}
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


def _value(value: object) -> AnyValue:
    # Only primitive allowlisted values enter encoding; no arbitrary recursive objects.
    if isinstance(value, bool):
        return AnyValue(bool_value=value)
    if isinstance(value, str):
        return AnyValue(string_value=value)
    if isinstance(value, int):
        return AnyValue(int_value=value)
    if isinstance(value, float):
        return AnyValue(double_value=value)
    if isinstance(value, tuple):
        return AnyValue(array_value=ArrayValue(values=[_value(item) for item in value]))
    raise ValueError("telemetry_invalid_attribute_type")


def _attributes(attributes: Mapping[str, object]) -> list[KeyValue]:
    return [KeyValue(key=key, value=_value(value)) for key, value in attributes.items()]


def _flags(context: SpanContext | None, trace_flags: int) -> int:
    return (
        trace_flags
        | (0x100 if context is not None else 0)
        | (0x200 if context is not None and context.is_remote else 0)
    )


def _safe_name(span: ReadableSpan, routes: frozenset[str]) -> str:
    if span.name in DOMAIN_SPAN_NAMES:
        return span.name
    attributes = filter_attributes(span.attributes, routes=routes)
    method = attributes.get("http.request.method", attributes.get("http.method"))
    if span.kind == SpanKind.SERVER:
        label = method if isinstance(method, str) else "HTTP"
        route = attributes.get("http.route")
        return f"{label} {route}" if route is not None else label
    # Contrib send/receive spans often omit http.route. Compare their names to
    # the router catalog, never accept a path merely because it looks like one.
    for suffix in (" http send", " http receive"):
        if span.name.endswith(suffix):
            prefix = span.name[: -len(suffix)]
            verb, _, route = prefix.partition(" ")
            if verb in HTTP_METHODS and route in routes:
                return span.name
            return f"HTTP{suffix}"
    return "unknown"


def project_span(span: ReadableSpan, *, routes: frozenset[str]) -> Span:
    """Read public SDK properties into a fresh allowlisted protocol message."""
    context = span.get_span_context()
    if context is None or not context.is_valid:
        raise ValueError("telemetry_invalid_span_context")
    parent = span.parent
    if span.start_time is None or span.end_time is None:
        raise ValueError("telemetry_unfinished_span")
    return Span(
        trace_id=context.trace_id.to_bytes(16, "big"),
        span_id=context.span_id.to_bytes(8, "big"),
        parent_span_id=parent.span_id.to_bytes(8, "big") if parent and parent.is_valid else b"",
        flags=_flags(parent, int(context.trace_flags)),
        name=_safe_name(span, routes),
        kind=Span.SpanKind.Value("SPAN_KIND_" + span.kind.name),
        start_time_unix_nano=span.start_time,
        end_time_unix_nano=span.end_time,
        attributes=_attributes(filter_attributes(span.attributes, routes=routes)),
        events=[
            Span.Event(
                name=event.name,
                time_unix_nano=event.timestamp,
                attributes=_attributes(filter_attributes(event.attributes, routes=routes)),
                dropped_attributes_count=event.dropped_attributes,
            )
            for event in span.events
            if event.name in EVENT_NAMES
        ],
        links=[
            Span.Link(
                trace_id=link.context.trace_id.to_bytes(16, "big"),
                span_id=link.context.span_id.to_bytes(8, "big"),
                flags=_flags(link.context, int(link.context.trace_flags)),
                dropped_attributes_count=link.dropped_attributes,
            )
            for link in span.links
            if link.context.is_valid
        ],
        status=Status(code=Status.StatusCode.Value("STATUS_CODE_" + span.status.status_code.name)),
        dropped_attributes_count=span.dropped_attributes,
        dropped_events_count=span.dropped_events,
        dropped_links_count=span.dropped_links,
    )


def build_request(
    spans: Sequence[ReadableSpan], *, service_name: str, routes: frozenset[str]
) -> ExportTraceServiceRequest:
    resource = {
        "telemetry.sdk.name": "opentelemetry",
        "telemetry.sdk.language": "python",
        "telemetry.sdk.version": _SDK_VERSION,
    }
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", service_name):
        resource["service.name"] = service_name
    scopes: dict[str, ScopeSpans] = {}
    for span in spans:
        scope = span.instrumentation_scope
        name = scope.name if scope and scope.name in _SCOPE_VERSIONS else "unknown"
        if name not in scopes:
            scopes[name] = ScopeSpans(
                scope=InstrumentationScope(name=name, version=_SCOPE_VERSIONS.get(name, ""))
            )
        scopes[name].spans.append(project_span(span, routes=routes))
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=_attributes(resource)),
                scope_spans=list(scopes.values()),
            )
        ]
    )


def _setting(suffix: str, default: str = "") -> str:
    return os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_" + suffix,
        os.environ.get("OTEL_EXPORTER_OTLP_" + suffix, default),
    )


class PrivacyOTLPSpanExporter(SpanExporter):
    """Encode only approved data, then transmit it using public protobuf/gRPC APIs.

    BatchSpanProcessor serializes export calls. Shutdown may interrupt retries;
    flush waits for an active call and does not claim successful delivery.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        service_name: str,
        route_templates: Callable[[], frozenset[str]] = registered_route_templates,
    ) -> None:
        parsed = urlparse(endpoint)
        self._target = parsed.netloc or endpoint
        self._insecure = (
            parsed.scheme != "https"
            and _setting("INSECURE", "true" if parsed.scheme == "http" else "false").lower()
            == "true"
        )
        self._timeout = float(_setting("TIMEOUT", "10"))
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
        self._route_templates = route_templates
        self._stopped = Event()
        self._export_lock = Lock()
        self._channel_lock = Lock()
        self._channel = self._open_channel()
        try:
            self._client = TraceServiceStub(self._channel)  # type: ignore[no-untyped-call]
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

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._export_lock:
            if self._stopped.is_set():
                return SpanExportResult.FAILURE
            try:
                request = build_request(
                    spans, service_name=self._service_name, routes=self._route_templates()
                )
                deadline = monotonic() + self._timeout
                for attempt in range(6):
                    remaining = deadline - monotonic()
                    if remaining <= 0 or self._stopped.is_set():
                        break
                    try:
                        response: ExportTraceServiceResponse = self._client.Export(
                            request, metadata=self._headers, timeout=remaining
                        )
                        if response.partial_success.rejected_spans:
                            logger.warning(
                                "Telemetry export partially rejected.",
                                extra={"telemetry_status": "partial_rejection"},
                            )
                            return SpanExportResult.FAILURE
                        return SpanExportResult.SUCCESS
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
                                self._client = TraceServiceStub(self._channel)  # type: ignore[no-untyped-call]
                        if not math.isfinite(delay) or delay < 0 or delay >= deadline - monotonic():
                            break
                        if self._stopped.wait(delay):
                            break
            except Exception:
                pass  # Fail closed, including encoding/config/transport errors; never log raw data.
            logger.warning("Telemetry export failed.", extra={"telemetry_status": "export_failed"})
            return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        acquired = self._export_lock.acquire(timeout=max(timeout_millis, 0) / 1000)
        if acquired:
            self._export_lock.release()
        return acquired

    def shutdown(self) -> None:
        with self._channel_lock:
            if not self._stopped.is_set():
                self._stopped.set()
                self._channel.close()
