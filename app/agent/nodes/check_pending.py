import time
from collections.abc import Callable
from datetime import UTC

from app.agent.nodes.workflow_lifecycle import (
    is_interruption_candidate,
    is_resume_request,
    looks_like_confirmation,
)
from app.agent.schemas import AgentErrorCategory
from app.agent.state import AgentState
from app.observability.metrics import get_metrics
from app.observability.tracing import span
from app.policies.confirmation import (
    Clock,
    belongs_to_context,
    is_expired,
    parse_confirmation,
    transition,
)
from app.policies.models import (
    PendingAction,
    PendingActionStatus,
    PolicyAuditEvent,
    PolicyOutcome,
    stable_policy_event_id,
)
from app.policies.repository import PolicyAuditRepository, append_policy_audit


def make_check_pending_node(
    clock: Clock, ttl_seconds: int, audit_repository: PolicyAuditRepository
) -> Callable[[AgentState], AgentState]:
    def check_pending(state: AgentState) -> AgentState:
        started = time.perf_counter()
        status = "error"
        try:
            with span("confirmation.evaluate") as confirmation_span:
                result = _check_pending(state, clock, ttl_seconds)
                _record_confirmation_event(state, result, clock, audit_repository)
                status = str(result.get("confirmation_status") or "none")
                confirmation_span.set_attribute("confirmation.result", status)
                action = result.get("pending_action") or state.get("pending_action")
                if action is not None:
                    confirmation_span.set_attribute("action.status", action.status.value)
                confirmation_span.set_attribute("action.expired", status == "expired")
                get_metrics().confirmation_results_total.add(1, {"result": status})
                return result
        finally:
            get_metrics().confirmation_validation_duration_seconds.record(
                time.perf_counter() - started,
                {"status": status},
            )

    return check_pending


def _record_confirmation_event(
    state: AgentState,
    result: AgentState,
    clock: Clock,
    audit_repository: PolicyAuditRepository,
) -> None:
    status = result.get("confirmation_status")
    action = result.get("pending_action") or state.get("pending_action")
    context = state.get("execution_context")
    if status not in {"confirmed", "rejected", "expired"} or action is None or context is None:
        return
    append_policy_audit(
        audit_repository,
        PolicyAuditEvent(
            event_id=stable_policy_event_id(
                state["agent_run_id"], action.action_id, "confirmation", str(status)
            ),
            agent_run_id=state["agent_run_id"],
            request_id=context.request_id,
            conversation_id=context.conversation_id,
            tenant_id=context.tenant_id,
            actor_id=context.principal.actor_id,
            actor_type=context.principal.actor_type,
            roles=list(context.principal.roles),
            effective_customer_id=context.effective_customer_id,
            action_id=action.action_id,
            tool_name=action.tool_name,
            risk_level=action.risk_level,
            policy_outcome=PolicyOutcome.REQUIRE_CONFIRMATION,
            reason_codes=[f"confirmation_{status}"],
            timestamp=clock.now(),
            stage="confirmation",
            confirmation_status=status,
        ),
    )


def _check_pending(state: AgentState, clock: Clock, ttl_seconds: int) -> AgentState:
    action = state.get("pending_action")
    current_message = _latest_user_message(state)
    parsed = parse_confirmation(current_message)
    if action is None:
        suspended = state.get("suspended_workflow")
        if (
            parsed == "confirmed"
            and isinstance(suspended, dict)
            and suspended.get("pending_action") is not None
        ):
            # "Okay, yes, cancel it" after a side question: bring the saved action back and
            # ask again. Resuming never confirms, so the customer still has to say yes to it.
            return {
                "confirmation_status": "resume_suspended",
                "workflow_resume_source": "explicit_user_resume",
            }
        if is_resume_request(current_message):
            if state.get("suspended_workflow") is None:
                return {
                    "confirmation_status": "resume_unavailable",
                    "workflow_resume_source": "explicit_user_resume",
                }
            return {
                "confirmation_status": "resume_suspended",
                "workflow_resume_source": "explicit_user_resume",
            }
        return {"confirmation_status": "no_pending" if parsed != "ambiguous" else "normal"}
    context = state.get("execution_context")
    if context is None or not belongs_to_context(action, context):
        return {
            "confirmation_status": "ownership_error",
            "last_error": "Pending action belongs to another execution context.",
            "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
        }
    if action.status == PendingActionStatus.PENDING:
        if is_expired(action, clock.now(), ttl_seconds) or _predates_ai_resume(action, state):
            return {
                "pending_action": transition(action, PendingActionStatus.EXPIRED),
                "confirmation_status": "expired",
            }
        if parsed == "confirmed":
            return {
                "pending_action": transition(action, PendingActionStatus.CONFIRMED),
                "confirmation_status": "confirmed",
            }
        if parsed == "rejected":
            return {
                "pending_action": transition(action, PendingActionStatus.REJECTED),
                "confirmation_status": "rejected",
                "workflow_state": "cancelled",
            }
        if is_interruption_candidate(current_message) or not looks_like_confirmation(
            current_message
        ):
            # Anything that is not an attempt to answer the confirmation ("thanks, that's
            # all") is read as a new turn; the pending action stays saved, never executed.
            return {
                "confirmation_status": "inspect_interruption",
                "workflow_interruption_pending": True,
            }
        return {"confirmation_status": "ambiguous"}
    if parsed == "confirmed":
        if action.status == PendingActionStatus.EXECUTED:
            return {
                "confirmation_status": "no_pending",
                "replay_detected": True,
                "idempotency_outcome": "replay_prevented",
                "decision_reason": "idempotency_replay_prevented",
                "workflow_state": "completed",
            }
        return {"confirmation_status": "no_pending"}
    return {"confirmation_status": "normal", "pending_action": None}


def _predates_ai_resume(action: PendingAction, state: AgentState) -> bool:
    """A confirmation from before a human handoff must not authorize a write after it."""

    resumed_at = state.get("conversation_ai_resumed_at")
    if resumed_at is None:
        return False
    created_at = action.created_at
    if created_at.tzinfo is not None:
        created_at = created_at.astimezone(UTC).replace(tzinfo=None)
    return created_at <= resumed_at


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "user":
            return message["content"]
    return ""
