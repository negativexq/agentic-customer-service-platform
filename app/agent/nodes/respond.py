from app.agent.decision_compiler import CompileStatus
from app.agent.handoff import HANDOFF_OFFER_MESSAGE
from app.agent.schemas import (
    AgentErrorCategory,
    AgentRequestType,
    Intent,
)
from app.agent.state import AgentState, WorkflowLifecycleState
from app.memory.policy import evaluate_candidate
from app.memory.schemas import MemoryCandidate, MemoryType
from app.policies.models import PendingAction, PendingActionStatus
from app.resilience.errors import FailureCategory
from app.resilience.fallbacks import degraded_message


def _order_id(state: AgentState) -> int | str | None:
    arguments = state.get("tool_arguments")
    if isinstance(arguments, dict):
        order_id = arguments.get("order_id")
        if isinstance(order_id, (int, str)) and str(order_id).strip():
            return order_id
    pending_action = state.get("pending_action")
    if pending_action is not None:
        order_id = pending_action.arguments.get("order_id")
        if isinstance(order_id, (int, str)) and str(order_id).strip():
            return order_id
    return None


DISABLED_ACTION_MESSAGE = (
    "I can't do that here right now. I can connect you with a support specialist "
    "who can help with it."
)


def _error_message(category: AgentErrorCategory | None, state: AgentState) -> str:
    if category is None:
        return "I couldn't complete that request."
    decision = state.get("policy_decision")
    if (
        category == AgentErrorCategory.POLICY_DENIED
        and decision is not None
        and "action_disabled_by_operator" in decision.reasons
    ):
        return DISABLED_ACTION_MESSAGE
    order_id = _order_id(state)
    resource_not_found = (
        f"I couldn't find order #{order_id} in our system. "
        "Please verify the order number and try again."
        if order_id is not None
        else "I couldn't find that resource in our system. Please verify the details and try again."
    )
    selected_tool = state.get("selected_tool")
    pending = state.get("pending_action")
    operation = selected_tool or (pending.tool_name if pending is not None else None)
    invalid_state = (
        "I can't cancel this order because it has already moved to a stage where "
        "cancellation is unavailable."
        if operation in {None, "cancel_order"}
        else "I can't complete this request because the resource is no longer in an eligible state."
    )
    return {
        AgentErrorCategory.RESOURCE_NOT_FOUND: resource_not_found,
        AgentErrorCategory.OWNERSHIP_VIOLATION: "I can't access another customer's information.",
        AgentErrorCategory.INVALID_STATE: invalid_state,
        AgentErrorCategory.DUPLICATE_ACTION: (
            "It looks like this refund request is already being processed."
        ),
        AgentErrorCategory.INVALID_TOOL_ARGUMENTS: (
            "I need a little more specific information to do that safely."
        ),
        AgentErrorCategory.UNKNOWN_TOOL: "I can't perform that operation.",
        AgentErrorCategory.POLICY_DENIED: (
            "I couldn't complete this request because it did not pass our verification "
            "checks. A support specialist can review it if needed."
        ),
        AgentErrorCategory.LLM_ERROR: (
            "I couldn't understand that request reliably. Please rephrase it."
        ),
        AgentErrorCategory.TOOL_ERROR: "The business operation could not be completed safely.",
        AgentErrorCategory.DEPENDENCY_ERROR: (
            "I couldn't complete that dependency operation safely."
        ),
        AgentErrorCategory.INTERNAL_ERROR: "The request could not be completed safely.",
        AgentErrorCategory.UNKNOWN_WRITE_OUTCOME: (
            "I couldn't confirm whether the action completed, so I won't repeat it automatically."
        ),
        AgentErrorCategory.RETRIEVAL_ERROR: (
            "The knowledge service is temporarily unavailable. Please try again."
        ),
        AgentErrorCategory.RERANKER_ERROR: (
            "The knowledge service could not rank results reliably. Please try again."
        ),
        AgentErrorCategory.CONFIRMATION_EXPIRED: (
            "That confirmation expired. Please request the action again."
        ),
        AgentErrorCategory.DEPENDENCY_FAILURE: (
            "I couldn't complete that dependency operation safely."
        ),
    }[category]


def _tool_message(tool_name: str, result: dict[str, object] | None) -> str:
    if tool_name == "get_customer_orders":
        items = result.get("items", []) if result else []
        count = len(items) if isinstance(items, list) else 0
        return f"I found {count} order(s) for this customer."
    if tool_name == "get_customer_tickets":
        items = result.get("items", []) if result else []
        count = len(items) if isinstance(items, list) else 0
        return f"I found {count} support ticket(s) for this customer."
    if tool_name == "get_customer":
        return "I found the customer record."
    if tool_name == "get_order":
        return "I found the order record."
    if tool_name == "get_ticket":
        return "I found the support ticket."
    if tool_name == "escalate_to_human":
        return "I'll connect you with a human support specialist."
    if tool_name == "create_support_ticket":
        return "Your support ticket was created."
    return "The requested business operation completed."


def respond(state: AgentState) -> AgentState:
    error_category = state.get("error_category")
    pending_action = state.get("pending_action")
    tool_name = state.get("selected_tool")
    memory_status = state.get("memory_operation_status")
    compile_result = state.get("compile_result")
    clarification_required = bool(
        compile_result is not None and compile_result.status == CompileStatus.CLARIFICATION_REQUIRED
    )
    if state.get("security_signal") in {
        "instruction_override_attempt",
        "authority_claim_attempt",
    }:
        message = (
            "I can help with your request, but I can't disable required safeguards or bypass "
            "the normal approval process. I can't bypass those safeguards."
        )
    elif state.get("security_signal") == "memory_security_override_attempt":
        message = (
            "I can remember preferences and support context, but I can't store permissions, "
            "roles, or authorization claims."
        )
    elif state.get("handoff_offered") and error_category is None:
        message = HANDOFF_OFFER_MESSAGE
    elif _suspended_retrieval_unavailable(state):
        message = _unavailable_suspended_workflow_message(state)
    elif state.get("write_outcome_unknown"):
        message = (
            "I couldn't confirm whether the action completed, so I won't repeat it automatically."
        )
    elif (
        _has_suspended_mutation(state)
        and state.get("knowledge_answer") is None
        and state.get("workflow_interruption_status") == "suspended"
    ):
        message = _suspended_interruption_pending_message(state)
    elif state.get("memory_summary_requested") and error_category is None:
        message = _memory_summary_message(state)
    elif state.get("knowledge_answer") is not None and error_category is None:
        message = _customer_knowledge_answer(state)
    elif state.get("failure_category") is not None and not (
        state.get("failure_category") == FailureCategory.MEMORY_FAILURE.value
        and memory_status is None
    ):
        try:
            failure_category = state["failure_category"]
            assert failure_category is not None
            message = degraded_message(
                FailureCategory(failure_category),
                knowledge_only=state.get("tool_result") is None,
            )
        except ValueError:
            message = "I couldn't complete that request safely. Please try again."
    elif memory_status == "persisted":
        message = "I’ll remember that for future support conversations."
    elif memory_status == "deduplicated":
        message = "I already had that preference and kept it current."
    elif memory_status == "reject":
        message = (
            "I can remember preferences and support context, but I can't store permissions, "
            "roles, or authorization claims."
        )
    elif memory_status == "require_explicit":
        message = "Please explicitly ask me to remember that before I store it."
    elif memory_status == "forgotten":
        message = "I forgot that memory."
    elif memory_status == "not_found":
        message = "I couldn’t find an active memory matching that request."
    elif error_category is not None:
        message = _error_message(error_category, state)
    elif state.get("confirmation_status") == "no_pending":
        if pending_action is not None and pending_action.status == PendingActionStatus.EXECUTED:
            message = "That action was already completed; I did not execute it again."
        else:
            message = "There is no pending action to confirm."
    elif state.get("confirmation_status") == "resume_unavailable":
        message = "There is no suspended request to continue."
    elif pending_action is not None:
        if pending_action.status == PendingActionStatus.PENDING:
            message = _confirmation_request_message(pending_action)
        elif pending_action.status == PendingActionStatus.REJECTED:
            message = "I did not execute that action."
        elif pending_action.status == PendingActionStatus.EXPIRED:
            message = "That confirmation expired. Please request the action again."
        elif pending_action.status == PendingActionStatus.EXECUTED:
            message = _tool_message(pending_action.tool_name, state.get("tool_result"))
        elif pending_action.status == PendingActionStatus.FAILED:
            message = _error_message(error_category, state)
        else:
            message = "That action is no longer available."
    elif state.get("confirmation_status") == "ambiguous":
        message = "Please reply with a clear yes or no to the pending action."
    elif state.get("request_type") == AgentRequestType.INFORMATIONAL:
        message = (
            "I can look up customers, orders, and support tickets, create tickets, "
            "and help route requests."
        )
    elif (
        state.get("workflow_active")
        and state.get("missing_required_fields")
        and state.get("intent") == Intent.ORDER_CANCEL
    ):
        message = "I can help cancel your order. Let me verify the order details first."
    elif (
        state.get("workflow_active")
        and state.get("missing_required_fields")
        and state.get("intent") == Intent.REFUND_REQUEST
    ):
        missing = set(state.get("missing_required_fields", []))
        if "order_id" in missing:
            message = "Could you provide your order number?"
        elif "reason" in missing:
            message = "Could you briefly tell me why you are requesting this refund?"
        else:
            message = "I need a little more information before I can continue safely."
    elif not tool_name and state.get("intent") == Intent.REFUND_REQUEST:
        message = (
            "I'm sorry to hear that your product arrived damaged. I can help with the refund "
            "process. Could you provide your order number?"
        )
    elif not tool_name and state.get("intent") == Intent.ORDER_LOOKUP:
        message = "I can help you check your order status. Could you provide your order number?"
    elif not tool_name and state.get("intent") == Intent.ORDER_CANCEL:
        message = "I can help cancel your order. Let me verify the order details first."
    elif state.get("intent") == Intent.HUMAN_ESCALATION and (
        clarification_required
        or (state.get("request_type") == AgentRequestType.ESCALATION and not tool_name)
    ):
        message = (
            "I can help connect you with a support specialist. Could you tell me the reason "
            "you would like to speak with someone?"
        )
    elif not tool_name and state.get("intent") == Intent.HUMAN_ESCALATION:
        message = "I'll connect you with a human support specialist."
    elif state.get("request_type") == AgentRequestType.UNCLEAR:
        message = (
            "Could you clarify whether you need an order, ticket, cancellation, refund, "
            "or human assistance?"
        )
    elif tool_name:
        message = _tool_message(tool_name, state.get("tool_result"))
    else:
        message = "Could you clarify what you need help with?"
    message = _apply_memory_context(state, message)
    return {
        "final_response": message,
        "messages": [{"role": "assistant", "content": message}],
        "workflow_state": _response_workflow_state(state),
    }


def _apply_memory_context(state: AgentState, message: str) -> str:
    if state.get("error_category") is not None:
        return message
    memories = state.get("memory_context", [])
    keys = {str(item.get("normalized_key")): str(item.get("content")) for item in memories}
    if keys.get("response_style") == "The customer prefers concise answers." and len(message) > 240:
        first_sentence = message.split(". ", 1)[0].strip()
        if first_sentence:
            message = first_sentence + "."
    latest = " ".join(
        item["content"] for item in state.get("messages", []) if item["role"] == "user"
    ).casefold()
    if keys.get("contact_channel") and ("contact" in latest or "reach" in latest):
        channel = "email" if "email" in keys["contact_channel"].casefold() else "SMS"
        message += f" I’ll use your preferred {channel} support channel."
    return message


def _memory_summary_message(state: AgentState) -> str:
    summaries: list[str] = []
    for item in state.get("memory_context", []):
        if not isinstance(item, dict):
            continue
        memory_type = str(item.get("memory_type", ""))
        if memory_type not in {
            MemoryType.PREFERENCE.value,
            MemoryType.SUPPORT_CONTEXT.value,
        }:
            continue
        content = item.get("content")
        key = item.get("normalized_key")
        if not isinstance(content, str) or not isinstance(key, str):
            continue
        try:
            candidate = MemoryCandidate(
                memory_type=MemoryType(memory_type),
                content=content,
                normalized_key=key,
                explicit_user_request=True,
            )
        except ValueError:
            continue
        if evaluate_candidate(candidate).outcome == "allow":
            summaries.append(content)
    if not summaries:
        return "I don't have any saved preferences or support context for you yet."
    return "I remember: " + " ".join(summaries[:3])


def _confirmation_request_message(action: PendingAction) -> str:
    if action.tool_name == "request_refund":
        return (
            "I can submit your refund request. Before I continue, I need your confirmation. "
            "Would you like me to proceed?"
        )
    if action.tool_name == "cancel_order":
        return (
            "I can submit your cancellation request. Before I continue, I need your "
            "confirmation. Would you like me to proceed?"
        )
    return (
        "I can submit this request. Before I continue, I need your confirmation. "
        "Would you like me to proceed?"
    )


def _customer_knowledge_answer(state: AgentState) -> str:
    answer = state.get("knowledge_answer") or ""
    prefix = "Based on the retrieved evidence: "
    if answer.startswith(prefix):
        answer = answer[len(prefix) :]
    for citation in state.get("citations", []):
        citation_id = citation.get("citation_id")
        if isinstance(citation_id, str) and citation_id:
            answer = answer.replace(f" [{citation_id}]", "")
    answer = " ".join(answer.split())
    if _has_suspended_mutation(state):
        label = _suspended_workflow_label(state)
        return (
            f"I can answer that first. {answer} Your {label} is still saved and waiting "
            f"for confirmation. After we finish your question, you can continue the {label}."
        )
    return answer


def _has_suspended_mutation(state: AgentState) -> bool:
    snapshot = state.get("suspended_workflow")
    return isinstance(snapshot, dict) and snapshot.get("pending_action") is not None


def _suspended_workflow_label(state: AgentState) -> str:
    snapshot = state.get("suspended_workflow")
    intent = snapshot.get("intent") if isinstance(snapshot, dict) else None
    if intent == Intent.ORDER_CANCEL:
        return "cancellation request"
    return "refund request"


def _suspended_retrieval_unavailable(state: AgentState) -> bool:
    return _has_suspended_mutation(state) and (
        state.get("error_category") == AgentErrorCategory.RETRIEVAL_ERROR
        or state.get("answer_grounding", {}).get("status") == "retrieval_unavailable"
    )


def _unavailable_suspended_workflow_message(state: AgentState) -> str:
    label = _suspended_workflow_label(state)
    return (
        "I couldn't find a reliable answer for that question right now. Your "
        f"{label} is still saved and waiting for confirmation. After we finish your "
        f"question, you can continue the {label}."
    )


def _suspended_interruption_pending_message(state: AgentState) -> str:
    label = _suspended_workflow_label(state)
    return (
        "I couldn't complete that question reliably right now. Your "
        f"{label} is still saved and waiting for confirmation. "
        f"It was not confirmed or executed; you can continue the {label} when you're ready."
    )


def _response_workflow_state(state: AgentState) -> WorkflowLifecycleState:
    if state.get("suspended_workflow") is not None:
        return "suspended"
    action = state.get("pending_action")
    if action is not None:
        if action.status in {PendingActionStatus.PENDING, PendingActionStatus.CONFIRMED}:
            return "waiting_confirmation"
        if action.status == PendingActionStatus.EXECUTED:
            return "completed"
        if action.status in {
            PendingActionStatus.REJECTED,
            PendingActionStatus.EXPIRED,
            PendingActionStatus.FAILED,
        }:
            return "cancelled"
    if state.get("workflow_active"):
        return "active"
    return "completed"
