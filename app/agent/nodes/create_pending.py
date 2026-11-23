from collections.abc import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.nodes.common import error_category
from app.agent.nodes.pending_payload import build_policy_inputs, hash_policy_inputs
from app.agent.state import AgentState
from app.policies.confirmation import Clock
from app.policies.models import PendingAction, PolicyOutcome
from app.tools.base import ToolError
from app.tools.orders import CancelOrderInput, validate_cancel_order
from app.tools.refunds import RequestRefundInput, validate_refund_request


def _business_state_error(
    session: Session | None, tool_name: str, arguments: dict[str, object], tenant_id: str
) -> AgentState | None:
    """Check the order can take this action before asking the customer to confirm it.

    Confirming something that is then refused (an order that already shipped, a refund that
    was already requested) wastes a turn. The same check runs again after confirmation.
    """

    if session is None:
        return None
    try:
        if tool_name == "cancel_order":
            validate_cancel_order(
                session, CancelOrderInput.model_validate(arguments), tenant_id=tenant_id
            )
        elif tool_name == "request_refund":
            validate_refund_request(
                session, RequestRefundInput.model_validate(arguments), tenant_id=tenant_id
            )
    except ToolError as error:
        return {
            "error_category": error_category(error),
            "last_error": str(error),
            "tool_execution_status": "failed",
        }
    except ValueError:
        return None
    return None


def make_create_pending_node(
    clock: Clock, session: Session | None = None
) -> Callable[[AgentState], AgentState]:
    def create_pending(state: AgentState) -> AgentState:
        decision = state["policy_decision"]
        tool_name = state["selected_tool"]
        if (
            decision is None
            or tool_name is None
            or decision.outcome != PolicyOutcome.REQUIRE_CONFIRMATION
        ):
            return {}
        context = state["execution_context"]
        refused = _business_state_error(
            session, tool_name, dict(state.get("tool_arguments", {})), context.tenant_id
        )
        if refused is not None:
            return refused
        action_id = state.get("action_id") or f"act_{uuid4().hex}"
        raw_intent = state.get("intent")
        intent = getattr(raw_intent, "value", raw_intent)
        arguments = dict(state.get("tool_arguments", {}))
        collected_entities = dict(state.get("collected_entities", {}))
        for key in ("order_id", "ticket_id", "reason", "category", "description"):
            value = arguments.get(key)
            if isinstance(value, (str, int, bool)):
                collected_entities.setdefault(key, value)
        policy_inputs = build_policy_inputs(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
            outcome=decision.outcome.value,
            risk_level=decision.risk_level,
            reasons=list(decision.reasons),
            required_conditions=list(decision.required_conditions),
        )
        action = PendingAction(
            action_id=action_id,
            conversation_id=context.conversation_id,
            tenant_id=context.tenant_id,
            actor_id=context.principal.actor_id,
            actor_type=context.principal.actor_type,
            effective_customer_id=context.effective_customer_id,
            tool_name=tool_name,
            arguments=arguments,
            risk_level=decision.risk_level,
            intent=str(intent) if intent is not None else None,
            collected_entities=collected_entities,
            validation_context={
                "grounding_status": state.get("grounding_status", "not_recorded"),
                "grounding_reference_type": state.get("grounding_reference_type"),
                "grounding_trusted_source": state.get("grounding_trusted_source"),
                "target_validation_status": state.get("target_validation_status", "not_recorded"),
                "decision_reason": state.get("decision_reason"),
            },
            policy_inputs=policy_inputs,
            policy_inputs_hash=hash_policy_inputs(policy_inputs),
            created_at=clock.now(),
        )
        return {
            "pending_action": action,
            "action_id": action_id,
            "tool_execution_status": "pending",
            "workflow_state": "waiting_confirmation",
            "workflow_id": state.get("workflow_id") or f"workflow:{state['agent_run_id']}",
        }

    return create_pending
