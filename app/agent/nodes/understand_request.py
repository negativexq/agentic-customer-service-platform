import re
from collections.abc import Callable
from typing import TypeGuard

from app.agent.llm.base import DecisionProposalProvider
from app.agent.nodes.workflow_lifecycle import (
    explicit_replacement_intent,
    has_conflicting_intents,
    is_authority_claim_attempt,
    is_cross_customer_access_attempt,
    is_instruction_override_attempt,
    is_memory_summary_request,
)
from app.agent.schemas import (
    AgentErrorCategory,
    AgentRequestType,
    Intent,
    ProviderRunMetadata,
    SemanticDecision,
    SemanticDecisionV3,
    StructuredDecision,
    normalize_semantic_decision,
)
from app.agent.state import AgentState
from app.memory.extraction import extract_memory_request
from app.memory.policy import classify_memory_security_message
from app.observability.tracing import span
from app.policies.models import PendingActionStatus
from app.resilience.config import ResilienceConfig
from app.resilience.control import ReliabilityController
from app.resilience.errors import ResilienceError, RetryExhaustedError
from app.resilience.retry import run_with_retry


def _security_block(
    state: AgentState,
    *,
    signal: str = "instruction_override_attempt",
    memory_rejection: bool = False,
) -> AgentState:
    request_type = AgentRequestType.UNCLEAR
    existing_action = state.get("pending_action")
    rejected_action = (
        existing_action.model_copy(update={"status": PendingActionStatus.REJECTED})
        if existing_action is not None and existing_action.status == PendingActionStatus.PENDING
        else existing_action
    )
    result: AgentState = {
        "semantic_decision": SemanticDecision(
            intent=Intent.UNKNOWN,
            request_type=request_type,
            reason=signal,
        ),
        "intent": Intent.UNKNOWN,
        "request_type": request_type,
        "selected_tool": None,
        "tool_arguments": {},
        "pending_action": rejected_action,
        "pending_workflow_decision": None,
        "missing_required_fields": [],
        "workflow_active": False,
        "workflow_interruption_pending": False,
        "workflow_interruption_status": "security_blocked",
        "workflow_state": "cancelled",
        "suspended_workflow": None,
        "superseded_workflow": None,
        "confirmation_status": "rejected" if rejected_action is not None else "blocked",
        "security_signal": signal,
        "decision_reason": signal,
        "error_category": AgentErrorCategory.POLICY_DENIED,
        "last_error": "The request attempted to cross a deterministic authority boundary.",
    }
    if memory_rejection:
        result.update(
            {
                "memory_operation_status": "reject",
                "memory_policy_outcome": "reject",
                "memory_security_signal": signal,
            }
        )
    return result


def make_security_boundary_node() -> Callable[[AgentState], AgentState]:
    """Reject bounded authority-override language before workflow processing."""

    def detect_security_boundary(state: AgentState) -> AgentState:
        message = _latest_user_message(state)
        memory_signal = classify_memory_security_message(message)
        if memory_signal is not None:
            return _security_block(state, signal=memory_signal, memory_rejection=True)
        if is_instruction_override_attempt(message):
            return _security_block(state)
        if is_authority_claim_attempt(message):
            return _security_block(state, signal="authority_claim_attempt")
        if is_cross_customer_access_attempt(message):
            return _ownership_block(state)
        return {"security_signal": None}

    return detect_security_boundary


_EXPLICIT_ORDER_NUMBER = re.compile(r"(?:\border\s*(?:number\s*)?#?|#|\bsipariş\w*\s*)(\d+)", re.I)


def make_understand_request_node(
    provider: DecisionProposalProvider,
    resilience_config: ResilienceConfig | None = None,
    decision_contract_version: str = "semantic_decision_v3",
    reliability_controller: ReliabilityController | None = None,
) -> Callable[[AgentState], AgentState]:
    def understand_request(state: AgentState) -> AgentState:
        message = _latest_user_message(state)
        pending_action = state.get("pending_action")
        previous_pending_intent = pending_action.intent if pending_action is not None else None
        replacement_intent = explicit_replacement_intent(message, previous_pending_intent)
        if replacement_intent is not None and pending_action is not None:
            # "Cancel order 4115 instead" names its own order; otherwise keep the prior one.
            named = _EXPLICIT_ORDER_NUMBER.search(message)
            order_id = int(named.group(1)) if named else pending_action.arguments.get("order_id")
            target = (
                {"type": "explicit_order", "order_id": order_id}
                if isinstance(order_id, int) and order_id > 0
                else None
            )
            replacement = SemanticDecision(
                intent=replacement_intent,
                request_type=AgentRequestType.WRITE_ACTION,
                target=target,
                reason="explicit_workflow_replacement",
            )
            return {
                "semantic_decision": replacement,
                "intent": replacement.intent,
                "request_type": replacement.request_type,
                "selected_tool": None,
                "tool_arguments": {},
                "decision_reason": replacement.reason,
                "requires_retrieval": False,
                "last_error": None,
                "error_category": None,
                "provider_metadata": _provider_metadata(provider),
                "security_signal": None,
            }
        if is_memory_summary_request(message):
            summary_decision = SemanticDecisionV3(
                # Keep the frozen semantic contract unchanged.  The explicit
                # read-only memory_summary_requested flag carries this local
                # UI intent without adding a transport enum value.
                intent=Intent.SUPPORT_FAQ,
                request_type=AgentRequestType.KNOWLEDGE_ONLY,
                reason="memory_summary_request",
            )
            return {
                "semantic_decision": normalize_semantic_decision(summary_decision),
                "intent": Intent.SUPPORT_FAQ,
                "request_type": AgentRequestType.KNOWLEDGE_ONLY,
                "selected_tool": None,
                "tool_arguments": {},
                "memory_summary_requested": True,
                "requires_retrieval": False,
                "decision_reason": "memory_summary_request",
                "last_error": None,
                "error_category": None,
                "provider_metadata": _provider_metadata(provider),
                "security_signal": None,
            }
        if has_conflicting_intents(message):
            conflict_decision = SemanticDecisionV3(
                intent=Intent.UNKNOWN,
                request_type=AgentRequestType.UNCLEAR,
                reason="conflicting_request_intents",
                clarification_required=True,
            )
            return {
                "semantic_decision": normalize_semantic_decision(conflict_decision),
                "intent": Intent.UNKNOWN,
                "request_type": AgentRequestType.UNCLEAR,
                "selected_tool": None,
                "tool_arguments": {},
                "decision_reason": "conflicting_request_intents",
                "last_error": None,
                "error_category": None,
                "provider_metadata": _provider_metadata(provider),
                "security_signal": None,
            }
        context = state["execution_context"]
        timeout_seconds = (resilience_config or ResilienceConfig()).llm_timeout_seconds
        with span(
            "llm.structured_decision",
            attributes={
                "llm.provider": type(provider).__name__,
                "llm.operation": "structured_decision",
            },
        ) as llm_span:
            try:
                decision = run_with_retry(
                    lambda: provider.decide(
                        messages=state.get("messages", []),
                        customer_id=context.effective_customer_id,
                        memory_context=state.get("memory_context", []),
                    ),
                    dependency="llm",
                    config=resilience_config,
                    controller=reliability_controller,
                    service_identity=f"llm:{type(provider).__name__}",
                    provider_rate_limit=True,
                    timeout_seconds=timeout_seconds,
                )
            except (RetryExhaustedError, ResilienceError) as error:
                llm_span.set_attribute("llm.status", "error")
                return {
                    "intent": Intent.UNKNOWN,
                    "request_type": AgentRequestType.UNCLEAR,
                    "last_error": "The request could not be classified.",
                    "error_category": AgentErrorCategory.LLM_ERROR,
                    "failure_category": error.category.value,
                    "recovery_action": "clarify",
                    "provider_metadata": _provider_metadata(provider),
                    "security_signal": None,
                }
            except (TypeError, ValueError):
                llm_span.set_attribute("llm.status", "error")
                return {
                    "intent": Intent.UNKNOWN,
                    "request_type": AgentRequestType.UNCLEAR,
                    "last_error": "The model response did not match the required schema.",
                    "error_category": AgentErrorCategory.LLM_ERROR,
                    "failure_category": "llm_malformed_output",
                    "recovery_action": "clarify",
                    "provider_metadata": _provider_metadata(provider),
                    "security_signal": None,
                }
            llm_span.set_attribute("llm.status", "ok")
        if not _matches_contract(decision, decision_contract_version):
            llm_span.set_attribute("llm.status", "error")
            return {
                "intent": Intent.UNKNOWN,
                "request_type": AgentRequestType.UNCLEAR,
                "last_error": "The provider returned the wrong decision contract.",
                "error_category": AgentErrorCategory.LLM_ERROR,
                "failure_category": "llm_contract_mismatch",
                "recovery_action": "clarify",
                "provider_metadata": _provider_metadata(provider),
                "security_signal": None,
            }
        if isinstance(decision, (SemanticDecision, SemanticDecisionV3)):
            return {
                "semantic_decision": normalize_semantic_decision(decision),
                "last_error": None,
                "error_category": None,
                "provider_metadata": _provider_metadata(provider),
            }
        extracted_candidate, extracted_key = extract_memory_request(_latest_user_message(state))
        return {
            "intent": decision.intent,
            "request_type": decision.request_type,
            "selected_tool": decision.tool_name,
            "tool_arguments": dict(decision.arguments),
            "decision_reason": decision.reason,
            "requires_retrieval": decision.requires_retrieval,
            "knowledge_query": decision.knowledge_query,
            "memory_candidate": decision.memory_candidate or extracted_candidate,
            "memory_key": decision.memory_key or extracted_key,
            "last_error": None,
            "error_category": None,
            "provider_metadata": _provider_metadata(provider),
            "security_signal": None,
        }

    return understand_request


def _provider_metadata(provider: DecisionProposalProvider) -> ProviderRunMetadata | None:
    value = getattr(provider, "last_call_metadata", None)
    if not isinstance(value, dict):
        return None
    safe = {
        key: value[key]
        for key in (
            "provider",
            "model",
            "latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
        )
        if key in value and value[key] is not None
    }
    return ProviderRunMetadata.model_validate(safe) if safe else None


def _matches_contract(
    decision: StructuredDecision | SemanticDecision | SemanticDecisionV3,
    contract_version: str,
) -> TypeGuard[StructuredDecision | SemanticDecision | SemanticDecisionV3]:
    if contract_version == "direct_tool_v1":
        return isinstance(decision, StructuredDecision)
    if contract_version == "semantic_decision_v2":
        return isinstance(decision, SemanticDecision)
    if contract_version == "semantic_decision_v3":
        return isinstance(decision, SemanticDecisionV3)
    return False


def _latest_user_message(state: AgentState) -> str:
    for message in reversed(state.get("messages", [])):
        if message["role"] == "user":
            return message["content"]
    return ""


def _ownership_block(state: AgentState) -> AgentState:
    return {
        "semantic_decision": SemanticDecision(
            intent=Intent.UNKNOWN,
            request_type=AgentRequestType.UNCLEAR,
            reason="cross_customer_access_attempt",
        ),
        "intent": Intent.UNKNOWN,
        "request_type": AgentRequestType.UNCLEAR,
        "selected_tool": None,
        "tool_arguments": {},
        "pending_action": None,
        "security_signal": "cross_customer_access_attempt",
        "decision_reason": "cross_customer_access_attempt",
        "error_category": AgentErrorCategory.OWNERSHIP_VIOLATION,
        "last_error": "The request crossed the authenticated customer scope.",
        "workflow_active": False,
        "workflow_state": "cancelled",
        "confirmation_status": "blocked",
    }
