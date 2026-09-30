"""Test-only capture at the protobuf/gRPC boundary; no SDK span reconstruction."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import grpc
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.trace import SpanContext, SpanKind, Status, StatusCode, TraceFlags, TraceState

from app.observability import export as export_module


def attributes(values: Any) -> dict[str, Any]:
    def value(item: Any) -> Any:
        kind = item.WhichOneof("value")
        if kind == "array_value":
            return tuple(value(child) for child in item.array_value.values)
        return getattr(item, kind)

    return {item.key: value(item.value) for item in values}


class WireExporter:
    def __init__(self) -> None:
        self.spans: list[Any] = []
        self.payloads: list[bytes] = []
        self.requests: list[ExportTraceServiceRequest] = []
        self.calls: list[dict[str, Any]] = []
        self.shutdowns = 0

    def close(self) -> None:
        self.shutdowns += 1

    def Export(
        self, request: ExportTraceServiceRequest, **kwargs: Any
    ) -> ExportTraceServiceResponse:
        # Round-trip the final serialized request, not the pre-export SDK representation.
        payload = request.SerializeToString()
        request = ExportTraceServiceRequest.FromString(payload)
        self.payloads.append(payload)
        self.requests.append(request)
        self.calls.append(kwargs)
        for resource in request.resource_spans:
            for scoped in resource.scope_spans:
                for span in scoped.spans:
                    context = SpanContext(
                        trace_id=int.from_bytes(span.trace_id, "big"),
                        span_id=int.from_bytes(span.span_id, "big"),
                        is_remote=False,
                        trace_flags=TraceFlags(span.flags & 255),
                        trace_state=TraceState(),
                    )
                    parent = None
                    if span.parent_span_id:
                        parent = SpanContext(
                            context.trace_id,
                            int.from_bytes(span.parent_span_id, "big"),
                            bool(span.flags & 512),
                            TraceFlags(span.flags & 255),
                            TraceState(),
                        )
                    self.spans.append(
                        SimpleNamespace(
                            name=span.name,
                            context=context,
                            parent=parent,
                            kind=SpanKind(span.kind - 1),
                            start_time=span.start_time_unix_nano,
                            end_time=span.end_time_unix_nano,
                            attributes=attributes(span.attributes),
                            status=Status(StatusCode(span.status.code)),
                            events=[
                                SimpleNamespace(name=e.name, attributes=attributes(e.attributes))
                                for e in span.events
                            ],
                            links=[
                                SimpleNamespace(
                                    context=SpanContext(
                                        int.from_bytes(link.trace_id, "big"),
                                        int.from_bytes(link.span_id, "big"),
                                        bool(link.flags & 512),
                                        TraceFlags(link.flags & 255),
                                        TraceState(),
                                    ),
                                    attributes=attributes(link.attributes),
                                )
                                for link in span.links
                            ],
                            resource=SimpleNamespace(
                                attributes=attributes(resource.resource.attributes),
                                schema_url=resource.schema_url,
                            ),
                            instrumentation_scope=SimpleNamespace(
                                name=scoped.scope.name,
                                version=scoped.scope.version or None,
                                schema_url=scoped.schema_url,
                                attributes=attributes(scoped.scope.attributes),
                            ),
                        )
                    )
        return ExportTraceServiceResponse()

    def get_finished_spans(self) -> tuple[Any, ...]:
        return tuple(self.spans)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(grpc, "insecure_channel", lambda *a, **kw: self)
        monkeypatch.setattr(grpc, "secure_channel", lambda *a, **kw: self)
        monkeypatch.setattr(export_module, "TraceServiceStub", lambda channel: self)

    def exporter(self, **kwargs: Any) -> export_module.PrivacyOTLPSpanExporter:
        with (
            patch.object(grpc, "insecure_channel", return_value=self),
            patch.object(export_module, "TraceServiceStub", return_value=self),
        ):
            return export_module.PrivacyOTLPSpanExporter(endpoint="http://localhost:4317", **kwargs)
