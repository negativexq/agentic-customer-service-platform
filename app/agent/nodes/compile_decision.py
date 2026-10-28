import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.agent.compiler_contracts import CompilerReasonCode
from app.agent.decision_compiler import (
    BusinessTargetResolver,
    CompiledDecision,
    CompileStatus,
    DecisionCompiler,
)
from app.agent.schemas import AgentErrorCategory, Intent, SemanticDecision
from app.agent.semantic_grounding import (
    GroundingStatus,
    SemanticGrounding,
    validate_semantic_grounding,
)
from app.agent.state import AgentState
from app.agent.target_admissibility import assess_target_admissibility
from app.observability.metrics import get_metrics
from app.observability.tracing import span


def make_compile_decision_node(
    session: Session, decision_contract_version: str = "semantic_decision_v2"
) -> Callable[[AgentState], AgentState]:
    compiler = DecisionCompiler(BusinessTargetResolver(session))

    def compile_decision(state: AgentState) -> AgentState:
        started = time.perf_counter()
        status = "skipped"
        decision = state.get("semantic_decision")
        if decision is None:
            get_metrics().decision_compile_duration_seconds.record(
                time.perf_counter() - started, {"status": status}
            )
            return {}
        assert isinstance(decision, SemanticDecision)
        workflow_id = state.get("workflow_id") or f"workflow:{state['agent_run_id']}"
        user_message = _conversation_user_message(state)
        restored_action = bool(state.get("pending_action_restored"))
        grounding = (
            _restored_grounding(state, decision)
            if restored_action
            else validate_semantic_grounding(decision, user_message)
        )
        admissibility = assess_target_admissibility(decision.intent, decision.target, grounding)
        result = compiler.compile(
            decision,
            state["execution_context"],
            grounding=grounding,
            user_message=user_message,
            restored_action=restored_action
            and state.get("grounding_status") == GroundingStatus.NOT_APPLICABLE.value,
        )
        with span(
            "decision.compile",
            attributes={
                "decision.contract.version": decision_contract_version,
                "semantic.intent": decision.intent.value,
                "semantic.grounding.status": grounding.status.value,
                "semantic.grounding.reference_type": grounding.reference_type or "none",
                "semantic.target_admissibility": admissibility.value,
                "compiler.status": result.status.value,
                "compiled.tool": result.selected_tool or "none",
            },
        ):
            pass
        status = "rejected" if result.status == CompileStatus.COMPILE_REJECTED else "accepted"
        get_metrics().decision_compile_duration_seconds.record(
            time.perf_counter() - started, {"status": status}
        )
        if result.status == CompileStatus.COMPILE_REJECTED:
            return {
                "intent": result.intent,
                "request_type": result.request_type,
                "selected_tool": None,
                "tool_arguments": {},
                "error_category": AgentErrorCategory.INVALID_TOOL_ARGUMENTS,
                "last_error": "The semantic request could not be compiled safely.",
                "decision_reason": result.reason,
                "compile_result": result,
                "grounding_status": grounding.status.value,
                "grounding_reference_type": grounding.reference_type,
                "grounding_trusted_source": grounding.trusted_source,
                "target_validation_status": admissibility.value,
                "previous_intent": decision.intent,
                "pending_workflow_decision": None,
                "missing_required_fields": [],
                "collected_entities": _collected_entities(decision),
                "workflow_active": False,
                "workflow_resume_status": None,
                "workflow_state": (
                    "suspended" if state.get("suspended_workflow") is not None else "completed"
                ),
                "workflow_id": workflow_id,
            }
        workflow_fields = _workflow_input_fields(result)
        workflow_is_active = bool(workflow_fields)
        validation_context: dict[str, object] = {
            "grounding_status": grounding.status.value,
            "grounding_reference_type": grounding.reference_type,
            "grounding_trusted_source": grounding.trusted_source,
            "target_validation_status": admissibility.value,
            "decision_reason": result.reason,
        }
        return {
            "intent": result.intent,
            "request_type": result.request_type,
            "selected_tool": result.selected_tool,
            "tool_arguments": result.tool_arguments,
            "requires_retrieval": result.requires_retrieval,
            "knowledge_query": result.knowledge_query,
            "memory_candidate": result.memory_candidate,
            "memory_key": result.memory_key,
            "decision_reason": result.reason,
            "compile_result": result,
            "grounding_status": grounding.status.value,
            "grounding_reference_type": grounding.reference_type,
            "grounding_trusted_source": grounding.trusted_source,
            "target_validation_status": admissibility.value,
            "previous_intent": decision.intent,
            "pending_workflow_decision": decision
            if result.status == CompileStatus.CLARIFICATION_REQUIRED
            else None,
            "missing_required_fields": workflow_fields,
            "collected_entities": _collected_entities(decision),
            "workflow_active": workflow_is_active,
            "workflow_resume_status": None,
            "workflow_state": (
                "suspended"
                if state.get("suspended_workflow") is not None
                else "active"
                if workflow_is_active
                else "completed"
            ),
            "workflow_id": workflow_id,
            "workflow_tool_arguments": dict(result.tool_arguments),
            "workflow_validation_context": validation_context,
            "workflow_policy_inputs": {},
            "error_category": None,
            "last_error": None,
        }

    return compile_decision


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "user":
            return message["content"]
    return ""


def _conversation_user_message(state: AgentState) -> str:
    return " ".join(
        message["content"] for message in state.get("messages", []) if message["role"] == "user"
    )


def _collected_entities(decision: SemanticDecision) -> dict[str, str | int | bool]:
    entities: dict[str, str | int | bool] = {}
    target = decision.target
    if target is not None:
        if target.order_id is not None:
            entities["order_id"] = target.order_id
        if target.ticket_id is not None:
            entities["ticket_id"] = target.ticket_id
    if decision.reason:
        entities["reason"] = decision.reason
    return entities


def _restored_grounding(state: AgentState, decision: SemanticDecision) -> SemanticGrounding:
    status_value = state.get("grounding_status", GroundingStatus.NOT_APPLICABLE.value)
    try:
        status = GroundingStatus(status_value)
    except ValueError:
        status = GroundingStatus.INVALID
    reference_type = state.get("grounding_reference_type")
    if reference_type not in {"explicit_order", "explicit_ticket", "latest_order", None}:
        reference_type = None
    trusted_source = state.get("grounding_trusted_source")
    if trusted_source != "current_user_message":
        trusted_source = None
    if decision.target is None:
        reference_type = None
    return SemanticGrounding(
        status=status,
        reference_type=reference_type,
        trusted_source=trusted_source,
    )


def _workflow_input_fields(result: CompiledDecision) -> list[str]:
    """Keep the current workflow input boundary, using compiler codes instead of copy.

    The persisted AgentState key stays compatible with suspended checkpoints.
    Other clarification metadata (memory, knowledge, contradictory requests) is
    descriptive and does not create an incremental business workflow.
    """
    if result.status != CompileStatus.CLARIFICATION_REQUIRED:
        return []
    workflow_codes = {
        CompilerReasonCode.ORDER_REQUIRED,
        CompilerReasonCode.TICKET_REQUIRED,
        CompilerReasonCode.REFUND_REASON_REQUIRED,
        CompilerReasonCode.REFUND_REASON_UNSUPPORTED,
        CompilerReasonCode.TICKET_DETAILS_REQUIRED,
        CompilerReasonCode.ESCALATION_DETAILS_REQUIRED,
    }
    targeted_business_request = (
        result.reason_code == CompilerReasonCode.TARGET_REQUIRED
        and result.intent in {Intent.ORDER_CANCEL, Intent.REFUND_REQUEST, Intent.ORDER_LOOKUP}
    )
    if result.reason_code in workflow_codes or targeted_business_request:
        return [field.value for field in result.clarification_fields]
    return []
