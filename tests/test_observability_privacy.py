import ast
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult
from opentelemetry.trace import (
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    TraceState,
)
from otel_capture import WireExporter

from app.core.config import Settings
from app.observability import tracing
from app.observability.attributes import set_safe_attributes
from app.observability.middleware import fastapi_telemetry
from app.observability.privacy import (
    DOMAIN_SPAN_NAMES,
    NODE_NAMES,
    TOOL_NAMES,
    filter_attributes,
    registered_route_templates,
    safe_exception_type,
)


@pytest.fixture
def pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TracerProvider, WireExporter]]:
    delegate = WireExporter()
    delegate.install(monkeypatch)
    settings = Settings.model_construct(
        otel_enabled=True, otel_service_name="agentic-customer-service-platform"
    )
    # Exercise the production builder and batch processor, without global registration.
    provider = tracing._build_tracer_provider(settings)
    monkeypatch.setattr(tracing, "get_tracer_provider", lambda: provider)
    try:
        yield provider, delegate
    finally:
        provider.shutdown()


def test_export_boundary_sanitizes_every_payload_field_without_mutating_sdk_spans(
    pipeline: tuple[TracerProvider, WireExporter],
) -> None:
    _, delegate = pipeline
    secret = "PRIVATE_CUSTOMER_TOKEN_CONTENT_892734"
    private_resource = Resource(
        {
            "service.name": secret,
            "host.name": secret,
            "process.command_line": secret,
            "process.executable.path": secret,
            "process.owner": secret,
            "user.name": secret,
            "deployment.environment.name": secret,
            "telemetry.sdk.version": secret,
        },
        schema_url=f"https://{secret}.invalid",
    )
    provider = TracerProvider(resource=private_resource, shutdown_on_exit=False)
    wrapper = delegate.exporter(service_name="agentic-customer-service-platform")
    provider.add_span_processor(BatchSpanProcessor(wrapper))
    parent_context = SpanContext(
        trace_id=0x1234,
        span_id=0x5678,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
        trace_state=TraceState([("vendor", secret)]),
    )
    context = trace.set_span_in_context(trace.NonRecordingSpan(parent_context))
    tracer = provider.get_tracer(
        secret, secret, schema_url=f"https://{secret}.invalid", attributes={"credential": secret}
    )
    run_id = str(uuid4())
    request_id = str(uuid4())
    private_keys = (
        "prompt",
        "user.message",
        "customer.message",
        "request.body",
        "response.body",
        "model.reasoning",
        "db.query.parameters",
        "http.response.header.set_cookie",
        "model.output",
        "rag.chunk.content",
        "memory.content",
        "tool.arguments",
        "http.request.header.authorization",
        "http.request.header.cookie",
        "authorization",
        "bearer",
        "jwt",
        "credentials",
        "customer.id",
        "customer.id_hash",
        "actor.id",
        "actor.id_hash",
        "conversation.id",
        "conversation.id_hash",
        "tenant.id",
        "agent.action_id",
        "checkpoint.thread_id",
        "memory.key",
        "url.full",
        "url.path",
        "url.query",
        "http.url",
        "http.target",
        "client.address",
        "host.name",
    )
    try:
        with tracer.start_as_current_span("agent.run", context=context) as parent:
            parent.set_attribute("agent.run_id", run_id)
            parent.set_attribute("request.id", request_id)
            parent.set_attribute("actor.type", "support_operator")
            parent.set_attribute("actor.roles", ["support_operator"])
            parent.set_attribute("rag.grounding.answer_confidence", 0.75)
            for key in private_keys:
                parent.set_attribute(key, secret)
            parent.set_attribute(secret, secret)
            parent.set_attribute("tool.name", secret)
            parent.set_attribute("rag.grounding.status", secret)
            parent.set_attribute("actor.roles", ["support_operator", secret])
            parent.set_status(Status(StatusCode.ERROR, secret))
            parent.record_exception(RuntimeError(secret))
            parent.add_event(secret, {"error.category": secret})
            parent.add_event("escalation.created", {"tool.arguments": secret})
            parent.add_link(parent_context, {"credentials": secret})
            with tracer.start_as_current_span(secret) as child:
                child.set_attribute("node.status", "ok")
        assert provider.force_flush(timeout_millis=5000)
        assert isinstance(parent, ReadableSpan)
        assert len(delegate.spans) == 2
        exported = next(span for span in delegate.spans if span.name == "agent.run")
        exported_child = next(span for span in delegate.spans if span.name == "unknown")
        assert exported.attributes == {
            "agent.run_id": run_id,
            "request.id": request_id,
            "actor.type": "support_operator",
            "rag.grounding.answer_confidence": 0.75,
        }
        assert exported.status.status_code == StatusCode.ERROR
        assert exported.status.description is None
        assert exported.context.trace_id == parent_context.trace_id
        assert exported.context.span_id == parent.get_span_context().span_id
        assert exported.parent is not None
        assert exported.parent.span_id == parent_context.span_id
        assert exported.parent.is_remote
        assert exported_child.parent is not None
        assert exported_child.parent.span_id == exported.context.span_id
        assert exported.context.trace_state == exported.parent.trace_state == TraceState()
        assert exported.links[0].context.trace_state == TraceState()
        assert exported.links[0].attributes == {}
        assert {event.name for event in exported.events} == {"exception", "escalation.created"}
        exception = next(event for event in exported.events if event.name == "exception")
        assert exception.attributes == {"exception.type": "RuntimeError"}
        assert exported.resource.attributes["service.name"] == "agentic-customer-service-platform"
        assert set(exported.resource.attributes) == {
            "service.name",
            "telemetry.sdk.name",
            "telemetry.sdk.language",
            "telemetry.sdk.version",
        }
        assert exported.resource.schema_url == ""
        assert exported.instrumentation_scope is not None
        assert exported.instrumentation_scope.name == "unknown"
        assert exported.instrumentation_scope.version is None
        assert exported.instrumentation_scope.schema_url == ""
        assert not exported.instrumentation_scope.attributes
        assert all(secret.encode() not in payload for payload in delegate.payloads)
        # The original SDK representation still contains raw data. The exporter
        # projects a new immutable payload; it never mutates SDK private fields.
        assert parent.attributes is not None
        assert parent.attributes["request.body"] == secret
        assert parent.status.description == secret
        assert parent.resource.attributes["host.name"] == secret
        assert exported.start_time == parent.start_time
        assert exported.end_time == parent.end_time
    finally:
        provider.shutdown()


@pytest.mark.parametrize("key", ["agent.run_id", "request.id"])
@pytest.mark.parametrize("value", ["private-identifier", "00000000", "a" * 200, "A" * 36])
def test_invalid_correlation_identifiers_are_dropped(key: str, value: str) -> None:
    assert filter_attributes({key: value}) == {}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tool.risk_level", 4),
        ("tool.risk_level", True),
        ("retry.attempt", -1),
        ("memory.result_count", 1_000_001),
        ("rag.grounding.citation_coverage", math.nan),
        ("rag.grounding.answer_confidence", math.inf),
        ("rag.grounding.answer_confidence", -0.1),
        ("rag.grounding.answer_confidence", 1.01),
        ("rag.grounding.answer_confidence", True),
        pytest.param("rag.grounding.answer_confidence", 10**10000, id="huge-integer-ratio"),
        ("actor.roles", ["support_operator"] * 21),
        ("actor.roles", ["private-role"]),
        ("memory.types", ["preference", "a" * 129]),
        ("memory.types", [123]),
        ("tool.name", "get_order" + "x" * 129),
        ("service.identity", "https://private.invalid"),
        ("dependency.name", "private-customer-123"),
        ("http.status_code", 999),
        ("policy.reason_codes", ["private-business-reason"]),
    ],
)
def test_unknown_overlong_and_out_of_bounds_diagnostics_are_dropped(key: str, value: Any) -> None:
    assert filter_attributes({key: value}) == {}


def test_helper_filters_content_and_preserves_bounded_failure_without_raw_exception(
    pipeline: tuple[TracerProvider, WireExporter],
) -> None:
    provider, delegate = pipeline
    secret = "PRIVATE_PROMPT_EXCEPTION_CONTENT"
    error = RuntimeError(secret)
    with pytest.raises(RuntimeError) as raised:
        with tracing.span(
            "tool.execute", attributes={"prompt": secret, "tool.name": "get_order"}
        ) as active:
            raise error
    assert raised.value is error
    assert isinstance(active, ReadableSpan)
    assert active.status.description is None
    assert [event.name for event in active.events] == ["application.exception"]
    assert provider.force_flush(timeout_millis=5000)
    exported = delegate.spans[0]
    assert exported.attributes == {"tool.name": "get_order"}
    assert exported.status.status_code == StatusCode.ERROR
    assert exported.status.description is None
    assert [(event.name, dict(event.attributes or {})) for event in exported.events] == [
        ("application.exception", {"error.type": "RuntimeError"})
    ]
    assert all(secret.encode() not in payload for payload in delegate.payloads)


def test_unknown_exception_type_is_mapped_to_fixed_unknown() -> None:
    private_error = type("PRIVATE_EXCEPTION_CLASS", (RuntimeError,), {})
    assert safe_exception_type(private_error("PRIVATE_MESSAGE")) == "unknown"
    assert safe_exception_type("private.module.RuntimeError") == "RuntimeError"
    assert filter_attributes({"error.type": "PRIVATE_EXCEPTION_CLASS"}) == {"error.type": "unknown"}


def test_known_scope_and_environment_resource_fields_cannot_bypass_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "PRIVATE_SCOPE_VERSION_ENV_RESOURCE"
    delegate = WireExporter()
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES", f"host.name={secret},process.command_line={secret}"
    )
    delegate.install(monkeypatch)
    provider = tracing._build_tracer_provider(
        Settings.model_construct(
            otel_enabled=True, otel_service_name="agentic-customer-service-platform"
        )
    )
    try:
        assert provider.resource.attributes["host.name"] == secret
        tracer = provider.get_tracer(
            "agentic-customer-service-platform",
            secret,
            schema_url=f"https://{secret}.invalid",
            attributes={"request.body": secret},
        )
        with tracer.start_as_current_span("agent.run"):
            pass
        assert provider.force_flush(timeout_millis=5000)
        scope = delegate.spans[0].instrumentation_scope
        assert scope is not None
        assert scope.name == "agentic-customer-service-platform"
        assert scope.version is None
        assert scope.schema_url == ""
        assert not scope.attributes
        assert "host.name" not in delegate.spans[0].resource.attributes
        assert all(secret.encode() not in payload for payload in delegate.payloads)
    finally:
        provider.shutdown()


def test_allowed_domain_diagnostics_survive_the_export_boundary(
    pipeline: tuple[TracerProvider, WireExporter],
) -> None:
    provider, delegate = pipeline
    attributes = {
        "actor.roles": ["support_operator", "customer"],
        "policy.reason_codes": ["customer_impacting_write"],
        "policy.outcome": "require_confirmation",
        "tool.name": "cancel_order",
        "tool.operation_type": "write",
        "tool.risk_level": 2,
        "confirmation.result": "confirmed",
        "memory.types": ["preference"],
        "memory.result_count": 2,
        "memory.status": "persisted",
        "rag.grounding.citation_coverage": 0.5,
        "rag.grounding.unsupported_claim_count": 0,
        "rag.grounding.retrieval_count": 3,
        "rag.grounding.accepted": True,
        "failure.category": "llm_timeout",
        "service.identity": "llm:OpenAICompatibleProvider",
        "retry.attempt": 1,
        "retry.exhausted": False,
        "recovery.action": "retry",
        "error.category": "dependency_error",
    }
    for name in DOMAIN_SPAN_NAMES:
        with tracing.span(name, attributes=attributes):
            pass
    assert provider.force_flush(timeout_millis=5000)
    assert {span.name for span in delegate.spans} == DOMAIN_SPAN_NAMES
    for span in delegate.spans:
        assert span.attributes == filter_attributes(attributes)


def test_long_route_templates_are_rejected() -> None:
    route = "/" + "a" * 256
    assert filter_attributes({"http.route": route}, routes=frozenset({route})) == {}


def test_safe_attribute_helper_drops_private_values_before_sdk_recording(
    pipeline: tuple[TracerProvider, WireExporter],
) -> None:
    provider, _ = pipeline
    with provider.get_tracer("agentic-customer-service-platform").start_as_current_span(
        "agent.run"
    ) as active:
        assert isinstance(active, ReadableSpan)
        set_safe_attributes(active, {"customer.id": 123, "prompt": "PRIVATE", "node.status": "ok"})
        assert active.attributes == {"node.status": "ok"}


@pytest.mark.parametrize("failure", [False, True])
def test_current_framework_exports_only_router_templates_and_bounded_exceptions(
    monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    secret = "PRIVATE_URL_BODY_AUTHORIZATION_EXCEPTION"
    delegate = WireExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    application = FastAPI(telemetry=fastapi_telemetry(Settings(otel_enabled=True), provider))
    router = APIRouter(prefix="/customers")
    monkeypatch.setattr(tracing, "get_tracer_provider", lambda: provider)

    @router.get("/{customer_id}")
    def endpoint(customer_id: str) -> dict[str, bool]:
        with tracing.span("agent.run"):
            if failure:
                raise RuntimeError(secret)
        return {"ok": True}

    application.include_router(router)
    routes = frozenset(
        route.path_format
        for route in iter_route_contexts(application.routes)
        if route.path_format is not None
    )
    wrapper = delegate.exporter(
        service_name="agentic-customer-service-platform", route_templates=lambda: routes
    )
    provider.add_span_processor(BatchSpanProcessor(wrapper))
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            response = client.get(
                f"/customers/{secret}?q={secret}", headers={"Authorization": f"Bearer {secret}"}
            )
            assert response.status_code == (500 if failure else 200)
            assert client.get(f"/unmatched/{secret}?q={secret}").status_code == 404
        assert provider.force_flush(timeout_millis=5000)
        servers = [span for span in delegate.spans if span.kind == SpanKind.SERVER]
        matched = next(
            span
            for span in servers
            if (span.attributes or {}).get("http.route") == "/customers/{customer_id}"
        )
        assert matched.name == "GET /customers/{customer_id}"
        unmatched = next(span for span in servers if "http.route" not in (span.attributes or {}))
        assert unmatched.name == "GET"
        assert all(span.status.description is None for span in delegate.spans)
        assert all(secret.encode() not in payload for payload in delegate.payloads)
        assert all("authorization" not in str(span.attributes).lower() for span in delegate.spans)
    finally:
        provider.shutdown()


def test_native_catalog_retains_only_fixed_operations_and_safe_scope_data(
    pipeline: tuple[TracerProvider, WireExporter],
) -> None:
    from importlib.metadata import version

    from app.observability.privacy import NATIVE_SPAN_NAMES

    provider, capture = pipeline
    secret = "PRIVATE_NATIVE_FUNCTION_SCOPE_VERSION"
    native = provider.get_tracer("fastapi", secret, attributes={"credential": secret})
    for name in sorted(NATIVE_SPAN_NAMES):
        with native.start_as_current_span(name) as active:
            active.set_attribute("code.function.name", secret)
            active.set_attribute("request.body", secret)
    with native.start_as_current_span(secret):
        pass
    with provider.get_tracer(secret).start_as_current_span("fastapi.endpoint"):
        pass
    assert provider.force_flush(5000)
    assert {span.name for span in capture.spans} == NATIVE_SPAN_NAMES | {"unknown"}
    assert sum(span.name == "unknown" for span in capture.spans) == 2
    for span in capture.spans:
        assert span.attributes == {}
        if span.instrumentation_scope.name == "fastapi":
            assert span.instrumentation_scope.version == version("fastapi")
            assert span.instrumentation_scope.attributes == {}
    assert all(secret.encode() not in payload for payload in capture.payloads)


def test_main_registers_nested_router_templates_not_paths() -> None:
    from app import main

    routes = registered_route_templates()
    assert "/customers/{customer_id}" in routes
    assert "/ui/agent-runs/{agent_run_id}" in routes
    assert "/agent/chat" in routes
    assert "/customers/1" not in routes
    assert routes == frozenset(
        route.path_format
        for route in iter_route_contexts(main.app.routes)
        if route.path_format is not None and len(route.path_format) <= 256
    )


def test_projection_failure_is_closed_and_logs_no_raw_details(
    pipeline: tuple[TracerProvider, WireExporter],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.observability import export as export_module

    provider, delegate = pipeline

    def fail(*_args: Any, **_kwargs: Any) -> ReadableSpan:
        raise RuntimeError("PRIVATE_PROJECTION_FAILURE")

    monkeypatch.setattr(export_module, "project_span", fail)
    with tracing.span("agent.run"):
        pass
    assert provider.force_flush(timeout_millis=5000)
    assert delegate.spans == []
    assert delegate.payloads == []
    assert "PRIVATE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_exporter_flush_shutdown_and_post_shutdown() -> None:
    delegate = WireExporter()
    exporter = delegate.exporter(service_name="agentic-customer-service-platform")
    assert exporter.export([]) == SpanExportResult.SUCCESS
    assert exporter.force_flush(1234)
    exporter.shutdown()
    exporter.shutdown()
    assert delegate.shutdowns == 1
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert len(delegate.requests) == 1


def test_transport_exception_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    delegate = WireExporter()
    exporter = delegate.exporter(service_name="agentic-customer-service-platform")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("PRIVATE_EXPORTER_FAILURE")

    monkeypatch.setattr(delegate, "Export", fail)
    assert exporter.export([]) == SpanExportResult.FAILURE
    assert "PRIVATE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_privacy_catalog_preserves_every_current_domain_span_name_and_tool() -> None:
    from app.tools.registry import TOOL_REGISTRY

    root = Path(__file__).resolve().parents[1]
    graph = ast.parse((root / "app/agent/graph.py").read_text())
    nodes = {
        call.args[0].value
        for call in ast.walk(graph)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_instrument_node"
        and call.args
        and isinstance(call.args[0], ast.Constant)
    }
    assert nodes == NODE_NAMES
    assert frozenset(TOOL_REGISTRY) == TOOL_NAMES
    for source in (root / "app").rglob("*.py"):
        tree = ast.parse(source.read_text())
        for call in ast.walk(tree):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "span"
                and call.args
            ):
                name = call.args[0]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    assert name.value in DOMAIN_SPAN_NAMES, (source, name.value)
    assert {"resilience.retry", "resilience.recovery"} <= DOMAIN_SPAN_NAMES


@pytest.mark.parametrize("explicit_cause", [False, True])
def test_chained_domain_exception_has_no_cause_context_or_serialized_content(
    pipeline: tuple[TracerProvider, WireExporter],
    explicit_cause: bool,
) -> None:
    import json
    from urllib.parse import quote

    provider, capture = pipeline
    cause = 'PRIVATE_CAUSE_"customer"/password? λ'
    outer = 'PRIVATE_OUTER_"prompt"/token? λ'
    values = [
        cause,
        outer,
        quote(cause, safe=""),
        quote(outer, safe=""),
        json.dumps({"prompt": outer, "cause": cause}),
    ]
    with pytest.raises(RuntimeError):
        with tracing.span("agent.run"):
            try:
                raise ValueError(cause)
            except ValueError as error:
                if explicit_cause:
                    raise RuntimeError(outer) from error
                raise RuntimeError(outer)  # noqa: B904 -- exercise implicit exception context
    assert provider.force_flush(5000)
    assert capture.spans[0].events[0].attributes == {"error.type": "RuntimeError"}
    for payload in capture.payloads:
        assert all(value.encode() not in payload for value in values)


def test_framework_chained_exception_encoded_urls_json_and_captured_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import json
    from urllib.parse import quote

    from fastapi import Response
    from google.protobuf.json_format import MessageToJson

    secret = 'PRIVATE_CUSTOMER/"chat"? λ'
    cause = "PRIVATE_FRAMEWORK_CAUSE"
    encoded = quote(secret, safe="")
    serialized = json.dumps({"prompt": secret})
    jwt = ".".join(
        base64.urlsafe_b64encode(part.encode()).decode().rstrip("=")
        for part in (
            '{"alg":"HS256","typ":"JWT"}',
            json.dumps({"sub": secret}),
            "PRIVATE_SIGNATURE",
        )
    )
    capture = WireExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    application = FastAPI(telemetry=fastapi_telemetry(Settings(otel_enabled=True), provider))
    monkeypatch.setattr(tracing, "get_tracer_provider", lambda: provider)

    @application.post("/private/{customer_id:path}")
    def endpoint(customer_id: str, body: dict[str, str], response: Response) -> dict[str, str]:
        response.set_cookie("session", "PRIVATE_SET_COOKIE")
        with tracing.span("agent.run") as active:
            active.set_attribute("request.body", json.dumps(body))
            active.set_attribute("tool.arguments", serialized)
            if body.get("fail"):
                try:
                    raise ValueError(cause)
                except ValueError as error:
                    raise RuntimeError(secret) from error
        return {"model.output": secret}

    routes = frozenset({"/private/{customer_id:path}"})
    provider.add_span_processor(
        BatchSpanProcessor(capture.exporter(service_name="test", route_templates=lambda: routes))
    )
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            for fail in (False, True):
                response = client.post(
                    f"/private/{encoded}?token={encoded}",
                    json={"prompt": secret, "fail": "yes" if fail else ""},
                    headers={"Authorization": f"Bearer {jwt}", "Cookie": "session=PRIVATE_COOKIE"},
                )
                assert response.status_code == (500 if fail else 200)
            assert client.get(f"/unknown/{encoded}?token={encoded}").status_code == 404
        assert provider.force_flush(5000)
        values = (
            secret,
            encoded,
            serialized,
            jwt,
            cause,
            "PRIVATE_COOKIE",
            "PRIVATE_SET_COOKIE",
            json.dumps(secret)[1:-1],
        )
        for request, payload in zip(capture.requests, capture.payloads, strict=True):
            assert all(value.encode() not in payload for value in values)
            rendered = MessageToJson(request)
            assert all(value not in rendered for value in values)
        domains = [span for span in capture.spans if span.name == "agent.run"]
        servers = [span for span in capture.spans if span.kind == SpanKind.SERVER]
        assert len(domains) == 2
        for domain in domains:
            assert any(server.context.trace_id == domain.context.trace_id for server in servers)
        assert any(
            event.attributes.get("error.type") == "RuntimeError"
            for span in capture.spans
            for event in span.events
        )
    finally:
        provider.shutdown()
