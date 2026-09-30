from collections.abc import Callable

from opentelemetry.trace import Span
from sqlalchemy.orm import Session

from app.agent.errors import (
    RuntimeErrorClassification,
    RuntimeFailureSource,
    classify_runtime_error,
)
from app.agent.schemas import AgentErrorCategory, Intent
from app.agent.state import AgentState
from app.memory.schemas import MemorySource
from app.memory.service import MemoryService
from app.observability.metrics import get_metrics
from app.observability.tracing import span
from app.resilience.errors import FailureCategory


def make_memory_action_node(
    service: MemoryService, session: Session
) -> Callable[[AgentState], AgentState]:
    def memory_action(state: AgentState) -> AgentState:
        context = state.get("execution_context")
        if context is None:
            return _failed(
                AgentErrorCategory.POLICY_DENIED,
                "Authenticated execution context is required for memory.",
            )
        intent = state.get("intent")
        if intent == Intent.MEMORY_REMEMBER:
            candidate = state.get("memory_candidate")
            if candidate is None:
                return _failed(
                    AgentErrorCategory.INVALID_TOOL_ARGUMENTS,
                    "A specific memory was not provided.",
                )
            with span("memory.evaluate_candidate") as memory_span:
                memory_span.set_attribute("memory.operation", "remember")
                memory_span.set_attribute("memory.type", candidate.memory_type.value)
                try:
                    result = service.remember(
                        session,
                        context.effective_customer_id,
                        candidate,
                        source=MemorySource.USER_EXPLICIT,
                        tenant_id=context.tenant_id,
                    )
                except Exception as error:
                    classification = classify_runtime_error(
                        error, source=RuntimeFailureSource.MEMORY
                    )
                    _set_failure_telemetry(memory_span, "remember", classification)
                    return _memory_failure(classification)
                memory_span.set_attribute("memory.status", result.status)
                memory_span.set_attribute("memory.policy_outcome", result.status)
                if result.security_signal is not None:
                    memory_span.set_attribute("memory.security_signal", result.security_signal)
            if result.status in {"persisted", "deduplicated"}:
                with span(
                    "memory.persist",
                    attributes={
                        "memory.type": candidate.memory_type.value,
                        "memory.status": result.status,
                    },
                ):
                    pass
            if result.status in {"persisted", "deduplicated"}:
                get_metrics().memory_writes_total.add(1, {"status": result.status})
            else:
                get_metrics().memory_rejections_total.add(
                    1, {"reason": result.reason or result.status}
                )
            return {
                "memory_operation_status": result.status,
                "memory_policy_outcome": result.status,
                "decision_reason": result.reason,
                "security_signal": result.security_signal,
                "memory_security_signal": result.security_signal,
                "last_error": None,
                "error_category": None
                if result.status in {"persisted", "deduplicated"}
                else AgentErrorCategory.POLICY_DENIED,
            }
        if intent == Intent.MEMORY_FORGET:
            key = state.get("memory_key")
            if not key:
                return _failed(
                    AgentErrorCategory.INVALID_TOOL_ARGUMENTS,
                    "Please specify which memory to forget.",
                )
            with span(
                "memory.forget",
                attributes={"memory.operation": "forget"},
            ) as memory_span:
                try:
                    result = service.forget(
                        session,
                        context.effective_customer_id,
                        key,
                        tenant_id=context.tenant_id,
                    )
                except Exception as error:
                    classification = classify_runtime_error(
                        error, source=RuntimeFailureSource.MEMORY
                    )
                    _set_failure_telemetry(memory_span, "forget", classification)
                    return _memory_failure(classification)
                memory_span.set_attribute("memory.status", result.status)
                memory_span.set_attribute("memory.policy_outcome", result.status)
            if result.status == "forgotten":
                get_metrics().memory_forgets_total.add(1, {"status": result.status})
            return {
                "memory_operation_status": result.status,
                "memory_policy_outcome": result.status,
                "last_error": None if result.status == "forgotten" else result.reason,
                "error_category": None
                if result.status in {"forgotten", "not_found"}
                else AgentErrorCategory.POLICY_DENIED,
            }
        return _failed(AgentErrorCategory.POLICY_DENIED, "Memory operation was not recognized.")

    return memory_action


def _memory_failure(classification: RuntimeErrorClassification) -> AgentState:
    failure_category = classification.failure_category or FailureCategory.MEMORY_FAILURE.value
    return {
        "memory_operation_status": "failed",
        "memory_policy_outcome": "failed",
        "failure_category": failure_category,
        "recovery_action": "fail_safely",
        "error_category": classification.category,
        "last_error": "Persistent memory could not be updated.",
    }


def _set_failure_telemetry(
    memory_span: Span, operation: str, classification: RuntimeErrorClassification
) -> None:
    memory_span.set_attribute("memory.operation", operation)
    memory_span.set_attribute("memory.status", "failed")
    memory_span.set_attribute("memory.policy_outcome", "failed")
    memory_span.set_attribute("error.category", classification.category.value)
    if classification.failure_category is not None:
        memory_span.set_attribute("memory.failure_category", classification.failure_category)


def _failed(category: AgentErrorCategory, message: str) -> AgentState:
    return {
        "memory_operation_status": "rejected",
        "memory_policy_outcome": "reject",
        "last_error": message,
        "error_category": category,
    }
