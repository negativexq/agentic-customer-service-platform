import json
from collections.abc import Callable
from typing import cast

from opentelemetry.trace import Span
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.agent.nodes.common import error_category
from app.agent.nodes.pending_payload import (
    hash_policy_inputs,
    normalize_policy_inputs,
    policy_input_diff,
    restore_pending_arguments,
    restore_policy_inputs,
    serialize_policy_inputs,
)
from app.agent.schemas import AgentErrorCategory, Intent
from app.agent.state import AgentState
from app.agent.tool_catalog import get_agent_tool_definition
from app.observability.tracing import span
from app.policies.confirmation import Clock, belongs_to_context
from app.policies.engine import PolicyEngine
from app.policies.models import (
    PendingAction,
    PendingActionStatus,
    PolicyAuditEvent,
    PolicyOutcome,
    stable_policy_event_id,
)
from app.policies.repository import PolicyAuditRepository, append_policy_audit
from app.tools.base import ToolError
from app.tools.orders import CancelOrderInput, validate_cancel_order
from app.tools.refunds import RequestRefundInput, validate_refund_request


def make_revalidate_node(
    session: Session,
    audit_repository: PolicyAuditRepository,
    clock: Clock,
    policy_engine: PolicyEngine,
) -> Callable[[AgentState], AgentState]:
    def revalidate(state: AgentState) -> AgentState:
        action = state.get("pending_action")
        with span(
            "policy.revalidate",
            attributes={"action.status": action.status.value if action else "none"},
        ) as policy_span:
            if action is None or action.status != PendingActionStatus.CONFIRMED:
                policy_span.set_attribute("policy.outcome", "deny")
                return _failed(
                    state,
                    AgentErrorCategory.POLICY_DENIED,
                    "There is no confirmable pending action.",
                )
            result = _revalidate_action(state, session, action, policy_span, policy_engine)
            _record_revalidation_event(state, result, action, audit_repository, clock)
            return result

    return revalidate


def _record_revalidation_event(
    state: AgentState,
    result: AgentState,
    action: PendingAction,
    audit_repository: PolicyAuditRepository,
    clock: Clock,
) -> None:
    context = state.get("execution_context")
    if context is None:
        return
    now = clock.now()
    allowed = result.get("error_category") is None
    append_policy_audit(
        audit_repository,
        PolicyAuditEvent(
            event_id=stable_policy_event_id(
                state["agent_run_id"],
                action.action_id,
                "policy_revalidation",
                "allow" if allowed else "deny",
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
            policy_outcome=PolicyOutcome.ALLOW if allowed else PolicyOutcome.DENY,
            reason_codes=["revalidated" if allowed else "revalidation_denied"],
            timestamp=now,
            stage="policy_revalidation",
            confirmation_status="confirmed",
            revalidation=True,
        ),
    )


def _revalidate_action(
    state: AgentState,
    session: Session,
    action: PendingAction,
    policy_span: Span,
    policy_engine: PolicyEngine,
) -> AgentState:
    context = state.get("execution_context")
    trace_fields: dict[str, str] = {}
    if context is None or not belongs_to_context(action, context):
        policy_span.set_attribute("policy.outcome", "deny")
        return _failed(
            state, AgentErrorCategory.OWNERSHIP_VIOLATION, "Pending action ownership failed."
        )
    try:
        definition = get_agent_tool_definition(action.tool_name)
        if definition is None:
            policy_span.set_attribute("policy.outcome", "deny")
            return _failed(
                state, AgentErrorCategory.UNKNOWN_TOOL, "Pending tool is not registered."
            )
        arguments_data = restore_pending_arguments(action)
        arguments = definition.input_model.model_validate(arguments_data)
        normalized_arguments = arguments.model_dump(mode="json")
        original_policy_inputs = dict(action.policy_inputs)
        restored_policy_inputs = restore_policy_inputs(action, context, normalized_arguments)
        original_policy_hash = action.policy_inputs_hash or hash_policy_inputs(
            original_policy_inputs
        )
        restored_policy_hash = hash_policy_inputs(restored_policy_inputs)
        input_diff = policy_input_diff(original_policy_inputs, restored_policy_inputs)
        trace_fields = {
            "original_pending_policy_inputs": serialize_policy_inputs(original_policy_inputs),
            "restored_policy_inputs": serialize_policy_inputs(restored_policy_inputs),
            "original_policy_inputs_normalized": normalize_policy_inputs(original_policy_inputs),
            "restored_policy_inputs_normalized": normalize_policy_inputs(restored_policy_inputs),
            "original_policy_inputs_hash": original_policy_hash,
            "restored_policy_inputs_hash": restored_policy_hash,
            "policy_input_diff": json.dumps(input_diff, sort_keys=True, separators=(",", ":")),
            "policy_revalidation_stage": "policy_input_comparison",
            "policy_revalidation_result": "matched",
        }
        if any(input_diff.values()):
            policy_span.set_attribute("policy.outcome", "deny")
            trace_fields["policy_revalidation_result"] = "input_mismatch"
            return _failed(
                state,
                AgentErrorCategory.POLICY_DENIED,
                "Pending policy inputs changed before revalidation.",
                **trace_fields,
            )
        requested_customer = getattr(arguments, "customer_id", None)
        if requested_customer != context.effective_customer_id:
            policy_span.set_attribute("policy.outcome", "deny")
            trace_fields["policy_revalidation_stage"] = "customer_scope_validation"
            trace_fields["policy_revalidation_result"] = "ownership_mismatch"
            return _failed(
                state,
                AgentErrorCategory.OWNERSHIP_VIOLATION,
                "Pending action customer scope failed.",
                **trace_fields,
            )
        trace_fields["policy_revalidation_stage"] = "policy_evaluation"
        policy = policy_engine.evaluate(
            tool_name=action.tool_name,
            context=context,
            arguments=normalized_arguments,
        )
        if policy.outcome == PolicyOutcome.DENY:
            policy_span.set_attribute("policy.outcome", "deny")
            trace_fields["policy_revalidation_result"] = "denied"
            return _failed(
                state,
                AgentErrorCategory.POLICY_DENIED,
                "Pending policy revalidation denied.",
                **trace_fields,
            )
        if policy.outcome not in {
            PolicyOutcome.ALLOW,
            PolicyOutcome.REQUIRE_CONFIRMATION,
        }:
            policy_span.set_attribute("policy.outcome", policy.outcome.value)
            trace_fields["policy_revalidation_result"] = "different_authority_path"
            return _failed(
                state,
                AgentErrorCategory.POLICY_DENIED,
                "Pending action requires a different authority path.",
                **trace_fields,
            )
        trace_fields["policy_revalidation_stage"] = "business_state_validation"
        if action.tool_name == "cancel_order":
            validate_cancel_order(
                session,
                CancelOrderInput.model_validate(arguments),
                tenant_id=context.tenant_id,
            )
        elif action.tool_name == "request_refund":
            validate_refund_request(
                session,
                RequestRefundInput.model_validate(arguments),
                tenant_id=context.tenant_id,
            )
        elif not _confirmable_without_state_check(action.tool_name):
            policy_span.set_attribute("policy.outcome", "deny")
            trace_fields["policy_revalidation_result"] = "unsupported_revalidation_tool"
            return _failed(
                state,
                AgentErrorCategory.POLICY_DENIED,
                "Pending tool is not revalidatable.",
                **trace_fields,
            )
        policy_span.set_attribute("policy.outcome", "allow")
        trace_fields["policy_revalidation_stage"] = "complete"
        trace_fields["policy_revalidation_result"] = "allowed"
        result: AgentState = {
            "selected_tool": action.tool_name,
            "tool_arguments": normalized_arguments,
            "decision_reason": action.validation_context.get("decision_reason"),
            "grounding_status": action.validation_context.get("grounding_status", "not_recorded"),
            "grounding_reference_type": action.validation_context.get("grounding_reference_type"),
            "grounding_trusted_source": action.validation_context.get("grounding_trusted_source"),
            "target_validation_status": "validated",
            "error_category": None,
            "last_error": None,
        }
        result.update(cast(AgentState, trace_fields))
        restored_intent = _restore_intent(action)
        if restored_intent is not None:
            result["intent"] = restored_intent
        return result
    except PydanticValidationError:
        policy_span.set_attribute("policy.outcome", "deny")
        trace_fields["policy_revalidation_stage"] = "argument_normalization"
        trace_fields["policy_revalidation_result"] = "invalid_arguments"
        return _failed(
            state,
            AgentErrorCategory.INVALID_TOOL_ARGUMENTS,
            "Pending arguments are invalid.",
            **trace_fields,
        )
    except ToolError as error:
        policy_span.set_attribute("policy.outcome", "deny")
        trace_fields["policy_revalidation_stage"] = "business_state_validation"
        trace_fields["policy_revalidation_result"] = error_category(error).value
        return _failed(state, error_category(error), str(error), **trace_fields)


def _confirmable_without_state_check(tool_name: str) -> bool:
    """Low-risk writes an operator put in confirm mode have no business-state precondition.

    Policy, ownership, and unchanged inputs were re-checked above, which is the whole
    revalidation such a tool needs.
    """

    from app.agent_config.schemas import ActionMode, allowed_action_modes

    return ActionMode.CONFIRM in allowed_action_modes(tool_name)


def _failed(
    state: AgentState,
    category: AgentErrorCategory,
    message: str,
    **metadata: object,
) -> AgentState:
    action = state.get("pending_action")
    result: AgentState = {
        "pending_action": action.model_copy(update={"status": PendingActionStatus.FAILED})
        if action is not None
        else None,
        "error_category": category,
        "last_error": message,
    }
    result.update(cast(AgentState, metadata))
    return result


def _restore_intent(action: PendingAction) -> Intent | None:
    if action.intent is None:
        return None
    try:
        return Intent(action.intent)
    except ValueError:
        return None
