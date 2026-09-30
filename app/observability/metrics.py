from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from opentelemetry import metrics
from opentelemetry.metrics import Meter, MeterProvider


@dataclass(frozen=True, slots=True)
class ObservabilityMetrics:
    authentication_attempts_total: Any
    agent_runs_total: Any
    agent_run_duration_seconds: Any
    decision_compile_duration_seconds: Any
    policy_evaluation_duration_seconds: Any
    confirmation_validation_duration_seconds: Any
    checkpoint_write_duration_seconds: Any
    idempotency_lookup_duration_seconds: Any
    tool_calls_total: Any
    tool_call_duration_seconds: Any
    tool_errors_total: Any
    rag_requests_total: Any
    rag_retrieval_duration_seconds: Any
    grounding_validation_duration_seconds: Any
    rag_grounding_citation_coverage: Any
    rag_grounding_unsupported_claim_count: Any
    rag_grounding_retrieval_count: Any
    rag_grounding_answer_confidence: Any
    policy_decisions_total: Any
    confirmation_results_total: Any
    escalations_total: Any
    agent_errors_total: Any
    memory_reads_total: Any
    memory_writes_total: Any
    memory_rejections_total: Any
    memory_forgets_total: Any
    memory_dlp_allowed: Any
    memory_dlp_redacted: Any
    memory_dlp_rejected: Any
    memory_sensitive_retrieval_blocked: Any
    dependency_failures_total: Any
    retry_attempts_total: Any
    retry_attempt_count: Any
    retry_exhausted_total: Any
    retry_exhausted: Any
    circuit_open: Any
    circuit_recovered: Any
    rate_limit_rejected: Any
    degraded_requests_total: Any
    tenant_isolation_decision: Any
    tenant_scoped_operation_status: Any


@dataclass(frozen=True, slots=True)
class OperationalSummary:
    """Process-local aggregate safe for operator diagnostics."""

    request_count: int
    request_error_count: int
    retry_count: int
    retry_exhausted_count: int
    circuit_open_count: int
    total_duration_seconds: float

    @property
    def error_rate(self) -> float:
        return self.request_error_count / self.request_count if self.request_count else 0.0

    @property
    def average_duration_ms(self) -> float:
        return (
            self.total_duration_seconds / self.request_count * 1000 if self.request_count else 0.0
        )


_summary_lock = Lock()
_summary_values = {
    "request_count": 0,
    "request_error_count": 0,
    "retry_count": 0,
    "retry_exhausted_count": 0,
    "circuit_open_count": 0,
    "total_duration_seconds": 0.0,
}


def build_metrics(meter: Meter | None = None) -> ObservabilityMetrics:
    active_meter = meter or metrics.get_meter("agentic-customer-service-platform")
    return ObservabilityMetrics(
        authentication_attempts_total=active_meter.create_counter(
            "authentication_attempts_total",
            unit="{attempt}",
            description="Authentication outcomes by bounded reason and principal type.",
        ),
        agent_runs_total=active_meter.create_counter(
            "agent_runs_total", unit="{run}", description="Agent runs."
        ),
        agent_run_duration_seconds=active_meter.create_histogram(
            "agent_run_duration_seconds", unit="s", description="Agent run duration."
        ),
        decision_compile_duration_seconds=active_meter.create_histogram(
            "decision_compile_duration_seconds",
            unit="s",
            description="Decision compiler duration by bounded outcome.",
        ),
        policy_evaluation_duration_seconds=active_meter.create_histogram(
            "policy_evaluation_duration_seconds",
            unit="s",
            description="Policy evaluation duration by bounded outcome.",
        ),
        confirmation_validation_duration_seconds=active_meter.create_histogram(
            "confirmation_validation_duration_seconds",
            unit="s",
            description="Confirmation validation duration by bounded outcome.",
        ),
        checkpoint_write_duration_seconds=active_meter.create_histogram(
            "checkpoint_write_duration_seconds",
            unit="s",
            description="Checkpoint persistence setup/write-path duration.",
        ),
        idempotency_lookup_duration_seconds=active_meter.create_histogram(
            "idempotency_lookup_duration_seconds",
            unit="s",
            description="Idempotency receipt lookup duration by bounded result.",
        ),
        tool_calls_total=active_meter.create_counter(
            "tool_calls_total", unit="{call}", description="Tool calls."
        ),
        tool_call_duration_seconds=active_meter.create_histogram(
            "tool_call_duration_seconds", unit="s", description="Tool call duration."
        ),
        tool_errors_total=active_meter.create_counter(
            "tool_errors_total", unit="{error}", description="Tool errors by safe category."
        ),
        rag_requests_total=active_meter.create_counter(
            "rag_requests_total", unit="{request}", description="RAG requests."
        ),
        rag_retrieval_duration_seconds=active_meter.create_histogram(
            "rag_retrieval_duration_seconds", unit="s", description="RAG retrieval duration."
        ),
        grounding_validation_duration_seconds=active_meter.create_histogram(
            "grounding_validation_duration_seconds",
            unit="s",
            description="Grounding validation duration by bounded outcome.",
        ),
        rag_grounding_citation_coverage=active_meter.create_histogram(
            "rag_grounding_citation_coverage",
            unit="1",
            description="Citation coverage of bounded grounded answers.",
        ),
        rag_grounding_unsupported_claim_count=active_meter.create_histogram(
            "rag_grounding_unsupported_claim_count",
            unit="{claim}",
            description="Unsupported claims rejected by grounding validation.",
        ),
        rag_grounding_retrieval_count=active_meter.create_histogram(
            "rag_grounding_retrieval_count",
            unit="{chunk}",
            description="Retrieved chunks considered by answer grounding.",
        ),
        rag_grounding_answer_confidence=active_meter.create_histogram(
            "rag_grounding_answer_confidence",
            unit="1",
            description="Bounded evidence-derived answer confidence.",
        ),
        policy_decisions_total=active_meter.create_counter(
            "policy_decisions_total", unit="{decision}", description="Policy decisions."
        ),
        confirmation_results_total=active_meter.create_counter(
            "confirmation_results_total", unit="{result}", description="Confirmation results."
        ),
        escalations_total=active_meter.create_counter(
            "escalations_total", unit="{escalation}", description="Human escalations."
        ),
        agent_errors_total=active_meter.create_counter(
            "agent_errors_total", unit="{error}", description="Agent errors by safe category."
        ),
        memory_reads_total=active_meter.create_counter(
            "memory_reads_total", unit="{read}", description="Memory reads."
        ),
        memory_writes_total=active_meter.create_counter(
            "memory_writes_total", unit="{write}", description="Memory writes."
        ),
        memory_rejections_total=active_meter.create_counter(
            "memory_rejections_total", unit="{rejection}", description="Rejected memory candidates."
        ),
        memory_forgets_total=active_meter.create_counter(
            "memory_forgets_total", unit="{forget}", description="Memory forget operations."
        ),
        memory_dlp_allowed=active_meter.create_counter(
            "memory_dlp_allowed",
            unit="{candidate}",
            description="Memory candidates allowed by structured DLP policy.",
        ),
        memory_dlp_redacted=active_meter.create_counter(
            "memory_dlp_redacted",
            unit="{candidate}",
            description="Memory candidates persisted only after bounded redaction.",
        ),
        memory_dlp_rejected=active_meter.create_counter(
            "memory_dlp_rejected",
            unit="{candidate}",
            description="Memory candidates rejected by structured DLP policy.",
        ),
        memory_sensitive_retrieval_blocked=active_meter.create_counter(
            "memory_sensitive_retrieval_blocked",
            unit="{retrieval}",
            description="Memory retrievals blocked by scope or sensitivity policy.",
        ),
        dependency_failures_total=active_meter.create_counter(
            "dependency_failures_total", unit="{failure}", description="Dependency failures."
        ),
        retry_attempts_total=active_meter.create_counter(
            "retry_attempts_total", unit="{attempt}", description="Retry attempts."
        ),
        retry_attempt_count=active_meter.create_counter(
            "retry_attempt_count",
            unit="{attempt}",
            description="Replay-safe dependency retry attempts.",
        ),
        retry_exhausted_total=active_meter.create_counter(
            "retry_exhausted_total", unit="{exhaustion}", description="Exhausted retries."
        ),
        retry_exhausted=active_meter.create_counter(
            "retry_exhausted",
            unit="{exhaustion}",
            description="Retry sequences stopped by attempts, deadline, or budget.",
        ),
        circuit_open=active_meter.create_counter(
            "circuit_open",
            unit="{event}",
            description="Dependency circuit open or open-state rejection events.",
        ),
        circuit_recovered=active_meter.create_counter(
            "circuit_recovered",
            unit="{event}",
            description="Dependency circuit half-open recoveries.",
        ),
        rate_limit_rejected=active_meter.create_counter(
            "rate_limit_rejected",
            unit="{rejection}",
            description="Bounded rate-limit rejections by non-identifying scope.",
        ),
        degraded_requests_total=active_meter.create_counter(
            "degraded_requests_total", unit="{request}", description="Degraded requests."
        ),
        tenant_isolation_decision=active_meter.create_counter(
            "tenant_isolation_decision",
            unit="{decision}",
            description="Tenant isolation decisions by bounded outcome.",
        ),
        tenant_scoped_operation_status=active_meter.create_counter(
            "tenant_scoped_operation_status",
            unit="{operation}",
            description="Tenant-scoped operation outcomes by bounded status.",
        ),
    )


_noop_metrics = build_metrics(
    metrics.NoOpMeterProvider().get_meter("agentic-customer-service-platform")
)
_metrics = _noop_metrics


def disable_metrics() -> None:
    """Disable domain recording without replacing or closing a global provider."""

    global _metrics
    _metrics = _noop_metrics


def get_metrics() -> ObservabilityMetrics:
    return _metrics


def get_operational_summary() -> OperationalSummary:
    with _summary_lock:
        return OperationalSummary(
            request_count=int(_summary_values["request_count"]),
            request_error_count=int(_summary_values["request_error_count"]),
            retry_count=int(_summary_values["retry_count"]),
            retry_exhausted_count=int(_summary_values["retry_exhausted_count"]),
            circuit_open_count=int(_summary_values["circuit_open_count"]),
            total_duration_seconds=float(_summary_values["total_duration_seconds"]),
        )


def record_agent_run_summary(*, duration_seconds: float, error: bool) -> None:
    with _summary_lock:
        _summary_values["request_count"] += 1
        _summary_values["request_error_count"] += int(error)
        _summary_values["total_duration_seconds"] += max(0.0, duration_seconds)


def record_retry_summary(*, exhausted: bool = False) -> None:
    with _summary_lock:
        _summary_values["retry_count"] += 1
        _summary_values["retry_exhausted_count"] += int(exhausted)


def record_circuit_open_summary() -> None:
    with _summary_lock:
        _summary_values["circuit_open_count"] += 1


def configure_metrics(meter_provider: MeterProvider) -> ObservabilityMetrics:
    global _metrics
    _metrics = build_metrics(meter_provider.get_meter("agentic-customer-service-platform"))
    return _metrics


def record_tenant_scope(*, decision: str, status: str) -> None:
    """Record bounded tenant-scope outcomes without tenant or customer attributes."""

    get_metrics().tenant_isolation_decision.add(1, {"decision": decision})
    get_metrics().tenant_scoped_operation_status.add(1, {"status": status})
