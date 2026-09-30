"""T2C current FastAPI/contrib baseline, scanned at the final OTLP boundary."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from otel_http_harness import HTTPHarness, HTTPPipeline
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.agent.state import ConversationMessage
from app.observability import tracing
from app.observability.metrics import get_operational_summary
from app.ui.trace import trace_event_key_for_node, trace_stage_for_node

MESSAGE = "Look up order 2. PRIVATE_T2C_CHAT_CONTENT"
CONVERSATION = "PRIVATE_T2C_CONVERSATION"
TRACE_ID = "1234567890abcdef1234567890abcdef"
PARENT_ID = "1234567890abcdef"
STAGES = {
    "load_context": "user_request",
    "understand_request": "intent_detection",
    "retrieve_memory": "memory_context",
    "retrieve_knowledge": "context_retrieval",
    "compile_decision": "grounding",
    "validate_tool": "target_validation",
    "evaluate_policy": "policy_evaluation",
    "policy_revalidate": "policy_evaluation",
    "inspect_risk": "policy_evaluation",
    "check_pending_action": "confirmation",
    "handle_workflow_interruption": "routing",
    "restore_suspended_workflow": "routing",
    "create_pending_action": "confirmation",
    "execute_tool": "execution_authority",
    "escalate": "execution_authority",
    "memory_action": "memory_context",
    "route_request": "routing",
    "respond": "response",
}


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> HTTPHarness:
    return HTTPHarness(monkeypatch, db_session)


def read_decision() -> StructuredDecision:
    return StructuredDecision(
        intent=Intent.ORDER_LOOKUP,
        request_type=AgentRequestType.READ_ACTION,
        tool_name="get_order",
        arguments={"customer_id": 1, "order_id": 2},
    )


def chat(
    pipeline: HTTPPipeline,
    *,
    message: str = MESSAGE,
    conversation: str = CONVERSATION,
    **kwargs: Any,
) -> Any:
    response = pipeline.client.post(
        "/agent/chat",
        json={"customer_id": 1, "conversation_id": conversation, "message": message},
        **kwargs,
    )
    assert response.status_code == 200, response.text
    return response


def request_graph(pipeline: HTTPPipeline, run_id: str) -> tuple[Any, list[Any]]:
    pipeline.flush()
    spans = pipeline.capture.spans
    domain = next(span for span in spans if span.attributes.get("agent.run_id") == run_id)
    related = [span for span in spans if span.context.trace_id == domain.context.trace_id]
    servers = [span for span in related if span.kind == SpanKind.SERVER]
    assert len(servers) == 1
    server = servers[0]
    assert server.attributes["http.route"] == "/agent/chat"
    assert server.name == "POST /agent/chat"
    assert len({span.context.span_id for span in related}) == len(related)
    # Descendant, not necessarily direct child: native operation spans may intervene in T3.
    by_id = {span.context.span_id: span for span in related}
    for span in related:
        if span.kind == SpanKind.SERVER:
            continue
        visited: set[int] = set()
        current = span
        while current.context.span_id != server.context.span_id:
            assert current.context.span_id not in visited
            visited.add(current.context.span_id)
            assert current.parent is not None
            assert current.parent.span_id in by_id
            current = by_id[current.parent.span_id]
    assert domain.attributes["request.id"] == pipeline.contexts[-1].request_id
    assert str(UUID(domain.attributes["request.id"])) == domain.attributes["request.id"]
    assert str(UUID(run_id)) == run_id
    return server, related


def test_chat_one_server_sync_dependency_context_and_ui_projection(harness: HTTPHarness) -> None:
    with harness.start([read_decision()]) as pipeline:
        caller = threading.get_ident()
        assert pipeline.runtime is not None and pipeline.runtime.settings.otel_enabled
        response = chat(pipeline)
        body = response.json()
        assert len(pipeline.contexts) == 1  # Nested production router invokes runtime exactly once.
        assert pipeline.worker_threads[0] != caller
        assert pipeline.worker_threads[0] != pipeline.loop_threads[0]
        assert pipeline.dependency_threads[0] != pipeline.loop_threads[0]
        server, related = request_graph(pipeline, body["agent_run_id"])
        assert {
            "agent.run",
            "agent.understand_request",
            "llm.structured_decision",
            "policy.evaluate",
            "tool.execute",
        } <= {span.name for span in related}
        assert len([span for span in related if span.name == "agent.run"]) == 1
        view = pipeline.projections.get_by_run_id(body["agent_run_id"])
        assert view is not None and view.trace_id == f"{server.context.trace_id:032x}"
        assert view.request_id == pipeline.contexts[0].request_id
        assert view.trace
        for event in view.trace:
            assert event.stage == STAGES.get(event.name, "internal")
            assert event.stage == trace_stage_for_node(event.name)
            assert event.event_key == trace_event_key_for_node(event.name)
        ui_response = pipeline.client.get(f"/ui/agent-runs/{body['agent_run_id']}")
        assert ui_response.status_code == 200
        assert ui_response.json()["trace_id"] == view.trace_id
        assert (
            "citations" not in body and "proposal" not in body and "provider_metadata" not in body
        )
        assert body["tool_call"] == {"status": "executed"}
        pipeline.assert_private_absent(MESSAGE, CONVERSATION)
        assert pipeline.factories == {"provider": 1, "exporter": 1}
        data = pipeline.metric_reader.get_metrics_data()
        assert data is not None
        names = {
            metric.name
            for resource in data.resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert {"agent_runs_total", "tool_calls_total"} <= names


def test_incoming_traceparent_preserves_http_domain_parentage(harness: HTTPHarness) -> None:
    with harness.start([read_decision()]) as pipeline:
        response = chat(pipeline, headers={"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01"})
        server, related = request_graph(pipeline, response.json()["agent_run_id"])
        assert server.context.trace_id == int(TRACE_ID, 16)
        assert server.parent is not None and server.parent.span_id == int(PARENT_ID, 16)
        assert server.parent.is_remote
        assert all(span.context.trace_id == int(TRACE_ID, 16) for span in related)
        assert all(span.context.trace_flags.sampled for span in related)
        pipeline.assert_private_absent(MESSAGE, CONVERSATION)


def test_write_confirmation_has_independent_request_traces_and_policy_revalidation(
    harness: HTTPHarness,
    db_session: Session,
) -> None:
    from app.models import Order
    from app.models.entities import OrderStatus

    decision = StructuredDecision(
        intent=Intent.ORDER_CANCEL,
        request_type=AgentRequestType.WRITE_ACTION,
        tool_name="cancel_order",
        arguments={"customer_id": 1, "order_id": 3},
    )
    with harness.start([decision]) as pipeline:
        pending = chat(pipeline, message="Cancel order 3").json()
        assert pending["pending_action"] is not None
        assert (
            not {
                "action_id",
                "conversation_id",
                "tenant_id",
                "actor_id",
                "actor_type",
                "effective_customer_id",
                "tool_name",
                "arguments",
                "risk_level",
                "intent",
                "collected_entities",
                "validation_context",
                "policy_inputs",
                "policy_inputs_hash",
                "created_at",
            }
            & pending["pending_action"].keys()
        )
        first, pending_spans = request_graph(pipeline, pending["agent_run_id"])
        assert not any(span.name == "tool.execute" for span in pending_spans)
        completed = chat(pipeline, message="Yes").json()
        second, confirmed_spans = request_graph(pipeline, completed["agent_run_id"])
        assert first.context.trace_id != second.context.trace_id
        assert {"confirmation.evaluate", "policy.revalidate", "tool.execute"} <= {
            span.name for span in confirmed_spans
        }
        assert "confirmed" in {
            span.attributes.get("confirmation.result") for span in confirmed_spans
        }
        order = db_session.get(Order, 3)
        assert order is not None and order.status == OrderStatus.CANCELLED
        assert len(pipeline.contexts) == 2
        pipeline.assert_private_absent(CONVERSATION)


@pytest.mark.parametrize("scenario", ["rag", "memory"])
def test_rag_and_memory_domain_catalogs_survive_http(harness: HTTPHarness, scenario: str) -> None:
    if scenario == "rag":
        decision = StructuredDecision(
            intent=Intent.REFUND_POLICY,
            request_type=AgentRequestType.KNOWLEDGE_ONLY,
            requires_retrieval=True,
            knowledge_query="refund policy",
        )
        message = "What is the refund policy?"
        expected = {
            "rag.retrieve",
            "rag.embed_query",
            "rag.dense_search",
            "rag.sparse_search",
            "rag.fusion",
            "rag.rerank",
            "rag.context_build",
            "rag.answer_generate",
        }
    else:
        decision = StructuredDecision(
            intent=Intent.MEMORY_REMEMBER, request_type=AgentRequestType.MEMORY_ACTION
        )
        message = "Remember that I prefer email updates."
        expected = {"memory.retrieve", "memory.evaluate_candidate", "memory.persist"}
    with harness.start([decision]) as pipeline:
        response = chat(pipeline, message=message)
        _, related = request_graph(pipeline, response.json()["agent_run_id"])
        assert expected <= {span.name for span in related}
        pipeline.assert_private_absent(message, CONVERSATION, "The customer prefers email updates.")


class FlakyDecisionProvider(FakeDecisionProvider):
    def __init__(self) -> None:
        super().__init__([read_decision()])
        self.attempts = 0

    def decide(
        self,
        *,
        messages: Sequence[ConversationMessage],
        customer_id: int,
        memory_context: Sequence[dict[str, object]] | None = None,
    ) -> StructuredDecision:
        self.attempts += 1
        if self.attempts == 1:
            raise TimeoutError("PRIVATE_T2C_RETRY_EXCEPTION")
        return super().decide(
            messages=messages, customer_id=customer_id, memory_context=memory_context
        )


def test_retry_span_inherits_http_trace_and_exception_is_private(harness: HTTPHarness) -> None:
    provider = FlakyDecisionProvider()
    with harness.start([], provider=provider) as pipeline:
        response = chat(pipeline)
        _, related = request_graph(pipeline, response.json()["agent_run_id"])
        assert provider.attempts == 2
        assert "resilience.retry" in {span.name for span in related}
        pipeline.assert_private_absent(MESSAGE, CONVERSATION, "PRIVATE_T2C_RETRY_EXCEPTION")


@pytest.mark.parametrize(
    "case,status",
    [
        ("oversized", 422),
        ("malformed", 422),
        ("missing", 422),
        ("unauthenticated", 401),
        ("forbidden", 403),
        ("cross_customer", 404),
        ("unmatched", 404),
    ],
)
def test_validation_auth_and_not_found_preserve_response_contract_and_privacy(
    harness: HTTPHarness,
    case: str,
    status: int,
) -> None:
    private = "PRIVATE_T2C_REJECTED_INPUT"
    with harness.start([]) as pipeline:
        body = {"customer_id": 1, "conversation_id": CONVERSATION, "message": private}
        if case == "oversized":
            body["message"] = private + "x" * 5001
        if case == "missing":
            body.pop("message")
        if case == "malformed":
            response = pipeline.client.post(
                "/agent/chat",
                content=f'{{"message":"{private}"',
                headers={"Content-Type": "application/json"},
            )
        elif case == "unmatched":
            response = pipeline.client.get(f"/PRIVATE_T2C_UNKNOWN_PATH?q={private}")
        else:
            if case == "unauthenticated":
                pipeline.client.headers.pop("Authorization")
            if case == "forbidden":
                pipeline.client.headers["Authorization"] = "Bearer forbidden-token"
            if case == "cross_customer":
                pipeline.client.headers["Authorization"] = "Bearer customer-token"
                body["customer_id"] = 2
            response = pipeline.client.post("/agent/chat", json=body)
        assert response.status_code == status
        if case == "oversized":
            assert response.json() == {
                "detail": {
                    "reason": "input_too_long",
                    "message": "Your message is too long. Please shorten it.",
                    "trace_event": "rejected_before_agent",
                }
            }
        if case == "unauthenticated":
            assert response.headers["www-authenticate"] == "Bearer"
            assert response.json() == {"detail": "Authentication required."}
        if case == "forbidden":
            assert response.json() == {"detail": "Insufficient permissions."}
        if case == "cross_customer":
            assert response.json() == {"detail": "Resource not found."}
        assert pipeline.contexts == []
        pipeline.assert_private_absent(
            private,
            CONVERSATION,
            "PRIVATE_T2C_UNKNOWN_PATH",
            "PRIVATE_CUSTOMER_ACTOR",
            "PRIVATE_FORBIDDEN_ACTOR",
        )
        servers = [span for span in pipeline.capture.spans if span.kind == SpanKind.SERVER]
        assert len(servers) == 1
        assert not any(span.name == "agent.run" for span in pipeline.capture.spans)
        assert (
            servers[0].attributes.get(
                "http.status_code", servers[0].attributes.get("http.response.status_code")
            )
            == status
        )


def test_escaped_domain_and_framework_chain_is_bounded_in_final_http_export(
    harness: HTTPHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with harness.start([]) as pipeline:
        assert pipeline.runtime is not None

        def fail(**kwargs: Any) -> Any:
            with tracing.span("agent.run"):
                try:
                    raise ValueError("PRIVATE_T2C_CAUSE")
                except ValueError as error:
                    raise RuntimeError("PRIVATE_T2C_ESCAPED_EXCEPTION") from error

        monkeypatch.setattr(pipeline.runtime, "run", fail)
        response = pipeline.client.post(
            "/agent/chat",
            json={"customer_id": 1, "conversation_id": CONVERSATION, "message": MESSAGE},
        )
        assert response.status_code == 500 and response.text == "Internal Server Error"
        pipeline.assert_private_absent(
            MESSAGE, CONVERSATION, "PRIVATE_T2C_CAUSE", "PRIVATE_T2C_ESCAPED_EXCEPTION"
        )
        domains = [span for span in pipeline.capture.spans if span.name == "agent.run"]
        servers = [span for span in pipeline.capture.spans if span.kind == SpanKind.SERVER]
        assert len(domains) == len(servers) == 1
        assert domains[0].context.trace_id == servers[0].context.trace_id
        assert domains[0].status.status_code.name == "ERROR"
        assert all(span.status.description is None for span in pipeline.capture.spans)
        assert domains[0].events[0].attributes == {"error.type": "RuntimeError"}


def test_health_readiness_and_system_health_keep_semantics_and_probe_eligibility(
    harness: HTTPHarness,
) -> None:
    with harness.start([]) as pipeline:
        assert pipeline.client.get("/health").json() == {"status": "ok"}
        ready = pipeline.client.get("/ready")
        assert ready.status_code == 200 and ready.json() == {"status": "ready"}
        health = pipeline.client.get("/ui/system-health")
        assert health.status_code == 200 and health.json()["status"] == "ready"
        pipeline.flush()
        servers = [span for span in pipeline.capture.spans if span.kind == SpanKind.SERVER]
        assert len(servers) == 3
        assert {span.attributes["http.route"] for span in servers} == {
            "/health",
            "/ready",
            "/ui/system-health",
        }
        assert len({span.context.trace_id for span in servers}) == 3
        assert pipeline.contexts == []
        pipeline.assert_private_absent()


def test_readiness_failure_remains_503_while_liveness_and_projection_stay_available(
    harness: HTTPHarness,
) -> None:
    from app.core.database import get_db

    class FailedSession:
        def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("PRIVATE_T2C_DATABASE_FAILURE")

    with harness.start([]) as pipeline:
        pipeline.app.dependency_overrides[get_db] = lambda: FailedSession()
        assert pipeline.client.get("/health").status_code == 200
        ready = pipeline.client.get("/ready")
        assert ready.status_code == 503 and ready.json() == {"status": "not_ready"}
        projected = pipeline.client.get("/ui/system-health")
        assert projected.status_code == 200 and projected.json()["status"] == "not_ready"
        assert "PRIVATE" not in projected.text
        pipeline.assert_private_absent("PRIVATE_T2C_DATABASE_FAILURE")


def test_disabled_http_ignores_external_provider_and_otlp_environment_keeps_local_summary(
    harness: HTTPHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://PRIVATE_T2C_ENDPOINT:4317")
    external_capture = InMemorySpanExporter()
    external = TracerProvider(shutdown_on_exit=False)
    external.add_span_processor(SimpleSpanProcessor(external_capture))
    before = get_operational_summary()
    try:
        with harness.start([read_decision()], enabled=False, external=external) as pipeline:
            response = chat(pipeline)
            assert pipeline.projections.get_by_run_id(response.json()["agent_run_id"]) is not None
            assert get_operational_summary().request_count == before.request_count + 1
            assert pipeline.capture.payloads == [] and external_capture.get_finished_spans() == ()
            assert pipeline.factories == {"provider": 0, "exporter": 0}
            assert pipeline.metric_reader.get_metrics_data() is None
        # Owned shutdown leaves the external provider usable.
        with external.get_tracer("external").start_as_current_span(
            "external_still_active"
        ) as active:
            assert active.is_recording()
        assert len(external_capture.get_finished_spans()) == 1
    finally:
        external.shutdown()


def test_http_lifespan_flushes_last_trace_closes_once_and_domain_becomes_noop(
    harness: HTTPHarness,
) -> None:
    with harness.start([read_decision()]) as pipeline:
        response = chat(pipeline)
        run_id = response.json()["agent_run_id"]
        # No manual flush: lifespan shutdown must drain the batch containing this request.
    assert any(span.attributes.get("agent.run_id") == run_id for span in pipeline.capture.spans)
    assert pipeline.events[-5:] == [
        "runtime_close",
        "checkpoint_close",
        "database_close",
        "telemetry_flush",
        "telemetry_close",
    ]
    assert pipeline.events.count("telemetry_close") == pipeline.capture.shutdowns == 1
    pipeline.owner.shutdown()
    assert pipeline.events.count("telemetry_close") == 1
    with (
        pipeline.owner.get_tracer_provider()
        .get_tracer("app")
        .start_as_current_span("agent.run") as active
    ):
        assert not active.is_recording()
    assert pipeline.factories == {"provider": 1, "exporter": 1}


def test_registration_failure_prevents_http_startup_and_any_trace_export(
    harness: HTTPHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(
        tracing.ObservabilityConfigurationError, match="telemetry_provider_registration_failed"
    ):
        with harness.start([], fail_registration=True):
            pytest.fail("failed telemetry registration allowed HTTP startup")
    capture, events, factories = harness.attempts[-1]
    assert capture.payloads == [] and capture.shutdowns == 1
    assert "runtime_close" not in events
    assert factories == {"provider": 1, "exporter": 1}
    assert "PRIVATE" not in caplog.text


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_http_shutdown_still_closes_pipeline_after_flush_failure(
    harness: HTTPHarness,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with harness.start([read_decision()]) as pipeline:
        response = chat(pipeline)
        run_id = response.json()["agent_run_id"]
        provider = pipeline.owner.get_tracer_provider()

        def fail_flush(timeout_millis: int = 30000) -> bool:
            pipeline.events.append("telemetry_flush_failed")
            if failure == "exception":
                raise RuntimeError("PRIVATE_T2C_FLUSH_FAILURE")
            return False

        monkeypatch.setattr(provider, "force_flush", fail_flush)
    assert pipeline.events[-5:] == [
        "runtime_close",
        "checkpoint_close",
        "database_close",
        "telemetry_flush_failed",
        "telemetry_close",
    ]
    assert pipeline.capture.shutdowns == 1
    assert any(span.attributes.get("agent.run_id") == run_id for span in pipeline.capture.spans)
    assert "PRIVATE_T2C_FLUSH_FAILURE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_multiple_chat_requests_do_not_reuse_http_context(harness: HTTPHarness) -> None:
    with harness.start([read_decision(), read_decision()]) as pipeline:
        first = chat(pipeline, conversation="t2c-first").json()
        first_server, _ = request_graph(pipeline, first["agent_run_id"])
        second = chat(pipeline, conversation="t2c-second").json()
        second_server, _ = request_graph(pipeline, second["agent_run_id"])
        assert first_server.context.trace_id != second_server.context.trace_id
        assert pipeline.contexts[0].request_id != pipeline.contexts[1].request_id
        assert len(pipeline.contexts) == 2
        assert pipeline.factories == {"provider": 1, "exporter": 1}


def test_memory_forget_keeps_domain_trace_without_exporting_memory_key(
    harness: HTTPHarness,
) -> None:
    decisions = [
        StructuredDecision(
            intent=Intent.MEMORY_REMEMBER, request_type=AgentRequestType.MEMORY_ACTION
        ),
        StructuredDecision(
            intent=Intent.MEMORY_FORGET,
            request_type=AgentRequestType.MEMORY_ACTION,
            memory_key="contact_channel",
        ),
    ]
    with harness.start(decisions) as pipeline:
        chat(pipeline, message="Remember that I prefer email updates.")
        response = chat(pipeline, message="Forget my contact preference")
        _, related = request_graph(pipeline, response.json()["agent_run_id"])
        assert "memory.forget" in {span.name for span in related}
        assert all("memory.key" not in span.attributes for span in related)
        pipeline.assert_private_absent("contact_channel", CONVERSATION)


def test_unsampled_incoming_parent_keeps_ui_correlation_without_trace_export(
    harness: HTTPHarness,
) -> None:
    with harness.start([read_decision()]) as pipeline:
        response = chat(pipeline, headers={"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-00"})
        pipeline.flush()
        assert pipeline.capture.requests == []
        view = pipeline.projections.get_by_run_id(response.json()["agent_run_id"])
        assert view is not None and view.trace_id == TRACE_ID
        assert view.trace  # Process-local projection is independent of export sampling.


def test_malformed_traceparent_does_not_corrupt_request_graph(harness: HTTPHarness) -> None:
    with harness.start([read_decision()]) as pipeline:
        response = chat(pipeline, headers={"traceparent": "PRIVATE_MALFORMED_PARENT"})
        server, _ = request_graph(pipeline, response.json()["agent_run_id"])
        assert server.parent is None
        assert server.context.is_valid
        pipeline.assert_private_absent("PRIVATE_MALFORMED_PARENT", MESSAGE, CONVERSATION)
