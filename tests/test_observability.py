from collections.abc import Iterator
from dataclasses import fields
from datetime import datetime

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.agent.llm.fake import FakeDecisionProvider
from app.agent.runtime import AgentRuntime
from app.agent.schemas import AgentRequestType, Intent, StructuredDecision
from app.auth.models import ActorType, Principal
from app.core.context import ExecutionContext
from app.memory.schemas import (
    MemoryCandidate,
    MemoryOperationResult,
    MemoryRetentionPolicy,
    MemorySource,
)
from app.memory.service import MemoryService
from app.observability import metrics as application_metrics
from app.observability import tracing
from app.observability.metrics import build_metrics, configure_metrics
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.resilience.errors import ResilienceError, RetryExhaustedError
from app.resilience.retry import run_with_retry


class FailingMemoryService(MemoryService):
    def __init__(self, operation: str, error: BaseException) -> None:
        super().__init__()
        self.operation = operation
        self.error = error

    def remember(
        self,
        session: Session,
        customer_id: int,
        candidate: MemoryCandidate,
        *,
        source: MemorySource = MemorySource.USER_EXPLICIT,
        now: datetime | None = None,
        retention_policy: MemoryRetentionPolicy = MemoryRetentionPolicy.STANDARD,
        tenant_id: str = "default",
    ) -> MemoryOperationResult:
        if self.operation == "remember":
            raise self.error
        return super().remember(
            session,
            customer_id,
            candidate,
            source=source,
            now=now,
            retention_policy=retention_policy,
            tenant_id=tenant_id,
        )

    def forget(
        self,
        session: Session,
        customer_id: int,
        normalized_key: str,
        now: datetime | None = None,
        tenant_id: str = "default",
    ) -> MemoryOperationResult:
        if self.operation == "forget":
            raise self.error
        return super().forget(session, customer_id, normalized_key, now=now, tenant_id=tenant_id)


@pytest.fixture
def telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[InMemorySpanExporter, InMemoryMetricReader]]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "get_tracer_provider", lambda: provider)
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader], shutdown_on_exit=False)
    monkeypatch.setattr(application_metrics, "_metrics", application_metrics.get_metrics())
    configure_metrics(meter_provider)
    try:
        yield exporter, metric_reader
    finally:
        provider.shutdown()
        meter_provider.shutdown()


def decision(
    intent: Intent,
    request_type: AgentRequestType,
    tool_name: str | None = None,
    arguments: dict[str, object] | None = None,
    *,
    requires_retrieval: bool = False,
    knowledge_query: str | None = None,
) -> StructuredDecision:
    return StructuredDecision(
        intent=intent,
        request_type=request_type,
        tool_name=tool_name,
        arguments=arguments or {},
        requires_retrieval=requires_retrieval,
        knowledge_query=knowledge_query,
        reason="observability test",
    )


def span_names(exporter: InMemorySpanExporter) -> set[str]:
    return {span.name for span in exporter.get_finished_spans()}


def span_attributes(exporter: InMemorySpanExporter) -> list[object]:
    return [
        value
        for span in exporter.get_finished_spans()
        for value in (span.attributes or {}).values()
    ]


def test_read_action_emits_root_and_tool_spans_without_sensitive_prompt(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, _ = telemetry
    message = "Look up order 2 with private customer details"
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_LOOKUP,
                    AgentRequestType.READ_ACTION,
                    "get_order",
                    {"customer_id": 1, "order_id": 2},
                )
            ]
        )
    )
    runtime.run(
        conversation_id="otel-read",
        customer_id=1,
        message=message,
        session=db_session,
    )
    names = span_names(exporter)
    assert {
        "agent.run",
        "agent.understand_request",
        "llm.structured_decision",
        "tool.execute",
    } <= names
    assert message not in span_attributes(exporter)
    assert 2 not in span_attributes(exporter)


def test_knowledge_request_emits_rag_spans_without_chunk_content(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, _ = telemetry
    content = "Delivered orders may qualify for refund review."
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.REFUND_POLICY,
                    AgentRequestType.KNOWLEDGE_ONLY,
                    requires_retrieval=True,
                    knowledge_query="refund policy",
                )
            ]
        )
    )
    runtime.run(
        conversation_id="otel-rag",
        customer_id=1,
        message="What is the refund policy?",
        session=db_session,
    )
    names = span_names(exporter)
    assert {
        "agent.run",
        "rag.retrieve",
        "rag.embed_query",
        "rag.dense_search",
        "rag.sparse_search",
        "rag.fusion",
        "rag.rerank",
        "rag.context_build",
        "rag.answer_generate",
    } <= names
    assert content not in span_attributes(exporter)
    answer_span = next(
        span for span in exporter.get_finished_spans() if span.name == "rag.answer_generate"
    )
    assert answer_span.attributes is not None
    assert answer_span.attributes["rag.grounding.citation_coverage"] == 1.0
    assert answer_span.attributes["rag.grounding.unsupported_claim_count"] == 0
    retrieval_count = answer_span.attributes["rag.grounding.retrieval_count"]
    assert isinstance(retrieval_count, int)
    assert retrieval_count >= 1


def test_confirmation_and_failure_spans_record_bounded_outcomes(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, _ = telemetry
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_CANCEL,
                    AgentRequestType.WRITE_ACTION,
                    "cancel_order",
                    {"customer_id": 1, "order_id": 3},
                )
            ]
        )
    )
    runtime.run(
        conversation_id="otel-confirm",
        customer_id=1,
        message="Cancel order 3",
        session=db_session,
    )
    runtime.run(
        conversation_id="otel-confirm",
        customer_id=1,
        message="Yes",
        session=db_session,
    )
    names = span_names(exporter)
    assert {
        "confirmation.evaluate",
        "policy.evaluate",
        "policy.revalidate",
        "tool.execute",
    } <= names
    assert "confirmed" in span_attributes(exporter)


def test_metrics_reader_observes_tool_and_policy_counters(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, metric_reader = telemetry
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_LOOKUP,
                    AgentRequestType.READ_ACTION,
                    "get_order",
                    {"customer_id": 1, "order_id": 1},
                )
            ]
        )
    )
    runtime.run(
        conversation_id="otel-metrics",
        customer_id=1,
        message="Look up order 1",
        session=db_session,
    )
    data = metric_reader.get_metrics_data()
    assert data is not None
    names = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"agent_runs_total", "tool_calls_total", "policy_decisions_total"} <= names
    assert span_names(exporter)


def test_capacity_metric_catalog_is_bounded() -> None:
    reader = InMemoryMetricReader()
    metric_set = build_metrics(MeterProvider(metric_readers=[reader]).get_meter("capacity-test"))
    instruments = (getattr(metric_set, field.name) for field in fields(metric_set))
    names = {item.name for item in instruments}
    assert {
        "agent_run_duration_seconds",
        "decision_compile_duration_seconds",
        "policy_evaluation_duration_seconds",
        "confirmation_validation_duration_seconds",
        "checkpoint_write_duration_seconds",
        "idempotency_lookup_duration_seconds",
        "rag_retrieval_duration_seconds",
        "grounding_validation_duration_seconds",
    } <= names


def test_circuit_and_rate_limit_emit_only_bounded_metric_dimensions(
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, metric_reader = telemetry
    now = 0.0
    config = ResilienceConfig(
        max_retries=0,
        circuit_failure_threshold=1,
        circuit_recovery_seconds=1,
        principal_rate_limit=1,
        customer_rate_limit=1,
    )
    controller = ReliabilityController(config, clock=lambda: now)
    with pytest.raises(RetryExhaustedError):
        run_with_retry(
            lambda: (_ for _ in ()).throw(TimeoutError("private provider detail")),
            dependency="retrieval",
            config=config,
            controller=controller,
            service_identity="retrieval:test",
        )
    now = 2.0
    assert (
        run_with_retry(
            lambda: "ok",
            dependency="retrieval",
            config=config,
            controller=controller,
            service_identity="retrieval:test",
        )
        == "ok"
    )
    context = ExecutionContext(
        request_id="bounded-metric-request",
        conversation_id="bounded-metric-conversation",
        principal=Principal(
            actor_id="private-actor-identifier",
            actor_type=ActorType.SUPPORT_OPERATOR,
            roles=["support_operator"],
        ),
        effective_customer_id=999,
    )
    controller.enforce_request_limits(context)
    with pytest.raises(ResilienceError):
        controller.enforce_request_limits(context)

    data = metric_reader.get_metrics_data()
    assert data is not None
    metrics = [
        metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    ]
    names = {metric.name for metric in metrics}
    assert {"circuit_open", "circuit_recovered", "rate_limit_rejected"} <= names
    values = {
        value
        for metric in metrics
        for point in metric.data.data_points
        for value in (point.attributes or {}).values()
    }
    assert "private-actor-identifier" not in values
    assert "private provider detail" not in values


def test_failed_tool_records_error_category_without_arguments(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, _ = telemetry
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                decision(
                    Intent.ORDER_LOOKUP,
                    AgentRequestType.READ_ACTION,
                    "get_order",
                    {"customer_id": 1, "order_id": 999},
                )
            ]
        )
    )
    runtime.run(
        conversation_id="otel-failure",
        customer_id=1,
        message="Look up an unavailable order",
        session=db_session,
    )
    tool_spans = [item for item in exporter.get_finished_spans() if item.name == "tool.execute"]
    assert tool_spans
    assert tool_spans[0].attributes is not None
    assert tool_spans[0].attributes.get("error.category") == "resource_not_found"
    assert 999 not in span_attributes(exporter)


def test_memory_spans_include_outcomes_but_not_memory_content(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, metric_reader = telemetry
    content = "The customer prefers a private channel that must not be telemetry."
    runtime = AgentRuntime(
        provider=FakeDecisionProvider(
            [
                StructuredDecision(
                    intent=Intent.MEMORY_REMEMBER,
                    request_type=AgentRequestType.MEMORY_ACTION,
                    reason="memory privacy",
                )
            ]
        ),
        memory_service=MemoryService(),
    )
    runtime.run(
        conversation_id="otel-memory",
        customer_id=1,
        message="Remember that I prefer email updates.",
        session=db_session,
    )
    names = span_names(exporter)
    assert {"memory.retrieve", "memory.evaluate_candidate", "agent.run"} <= names
    assert content not in span_attributes(exporter)
    assert "The customer prefers email updates." not in span_attributes(exporter)
    data = metric_reader.get_metrics_data()
    assert data is not None


def test_memory_operation_spans_record_reachable_success_and_failure_outcomes(
    db_session: Session, telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader]
) -> None:
    exporter, _ = telemetry
    successful_service = MemoryService()
    AgentRuntime(
        provider=FakeDecisionProvider(
            [
                StructuredDecision(
                    intent=Intent.MEMORY_REMEMBER,
                    request_type=AgentRequestType.MEMORY_ACTION,
                )
            ]
        ),
        memory_service=successful_service,
    ).run(
        conversation_id="otel-memory-remember-success",
        customer_id=1,
        message="Remember that I prefer email updates.",
        session=db_session,
    )
    AgentRuntime(
        provider=FakeDecisionProvider(
            [
                StructuredDecision(
                    intent=Intent.MEMORY_FORGET,
                    request_type=AgentRequestType.MEMORY_ACTION,
                    memory_key="contact_channel",
                )
            ]
        ),
        memory_service=successful_service,
    ).run(
        conversation_id="otel-memory-forget-success",
        customer_id=1,
        message="Forget my email preference.",
        session=db_session,
    )
    sentinel = "MEMORY_TELEMETRY_PRIVATE_SENTINEL_18"
    failing_remember = FailingMemoryService(
        "remember", OperationalError("write", {}, RuntimeError(sentinel))
    )
    AgentRuntime(
        provider=FakeDecisionProvider(
            [
                StructuredDecision(
                    intent=Intent.MEMORY_REMEMBER,
                    request_type=AgentRequestType.MEMORY_ACTION,
                )
            ]
        ),
        memory_service=failing_remember,
    ).run(
        conversation_id="otel-memory-remember-failure",
        customer_id=1,
        message=f"Remember {sentinel}.",
        session=db_session,
    )
    failing_forget = FailingMemoryService(
        "forget", OperationalError("delete", {}, RuntimeError(sentinel))
    )
    AgentRuntime(
        provider=FakeDecisionProvider(
            [
                StructuredDecision(
                    intent=Intent.MEMORY_FORGET,
                    request_type=AgentRequestType.MEMORY_ACTION,
                    memory_key="contact_channel",
                )
            ]
        ),
        memory_service=failing_forget,
    ).run(
        conversation_id="otel-memory-forget-failure",
        customer_id=1,
        message="Forget my email preference.",
        session=db_session,
    )

    memory_spans = [
        item
        for item in exporter.get_finished_spans()
        if item.name in {"memory.evaluate_candidate", "memory.forget"}
    ]
    operations = {(item.attributes or {}).get("memory.operation") for item in memory_spans}
    assert operations == {"remember", "forget"}
    assert any(
        item.name == "memory.evaluate_candidate"
        and (item.attributes or {}).get("memory.status") == "persisted"
        for item in memory_spans
    )
    assert any(
        item.name == "memory.forget" and (item.attributes or {}).get("memory.status") == "forgotten"
        for item in memory_spans
    )
    failed = [
        item for item in memory_spans if (item.attributes or {}).get("memory.status") == "failed"
    ]
    assert len(failed) == 2
    assert all(
        (item.attributes or {}).get("error.category") == "dependency_error" for item in failed
    )
    assert sentinel not in span_attributes(exporter)


def test_resilience_retry_emits_bounded_trace_and_metric(
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    exporter, metric_reader = telemetry
    attempts = 0

    def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("transient")
        return "ok"

    assert (
        run_with_retry(
            flaky,
            dependency="retrieval",
            config=ResilienceConfig(initial_backoff_ms=0, max_backoff_ms=0),
        )
        == "ok"
    )
    assert attempts == 2
    assert "resilience.retry" in span_names(exporter)
    data = metric_reader.get_metrics_data()
    assert data is not None
    names = {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }
    assert {"retry_attempt_count", "retry_attempts_total"} <= names
