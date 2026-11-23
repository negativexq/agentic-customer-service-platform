import re
from datetime import datetime

from app.agent.decision_compiler import CompileStatus
from app.agent.handoff import HANDOFF_OFFER_MESSAGE, customer_upset
from app.agent.nodes.create_pending import ALREADY_CANCELLED
from app.agent.schemas import (
    AgentErrorCategory,
    AgentRequestType,
    Intent,
)
from app.agent.state import AgentState, WorkflowLifecycleState
from app.memory.policy import evaluate_candidate
from app.memory.schemas import MemoryCandidate, MemoryType
from app.policies.models import PendingAction, PendingActionStatus
from app.rag.answer_generator import SAFE_UNCERTAINTY
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


# What the assistant can help with, for a question outside it ("What's 2+2?").
_OUT_OF_SCOPE = (
    "I can only help with your orders, refunds and cancellations, support tickets, and our "
    "store policies. What can I help you with?"
)

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
    operation = selected_tool or (
        "request_refund"
        if state.get("intent") == Intent.REFUND_REQUEST
        else (pending.tool_name if pending is not None else None)
    )
    subject = f"order #{order_id}" if order_id is not None else "this order"
    if state.get("last_error") == ALREADY_CANCELLED:
        invalid_state = (
            f"{subject[0].upper()}{subject[1:]} is already cancelled, "
            "so there's nothing more to do."
        )
    elif operation in {None, "cancel_order"}:
        invalid_state = (
            f"I can't cancel {subject} because it has already moved to a stage where "
            "cancellation is unavailable."
        )
    elif operation == "request_refund":
        invalid_state = (
            f"I can't start a refund for {subject} because it isn't in a refundable state. "
            "Refunds are considered after an order has been delivered."
        )
    else:
        invalid_state = (
            "I can't complete this request because the resource is no longer in an eligible state."
        )
    return {
        AgentErrorCategory.RESOURCE_NOT_FOUND: resource_not_found,
        # Saying "that belongs to someone else" would confirm the record exists.
        AgentErrorCategory.OWNERSHIP_VIOLATION: (
            f"I couldn't find order #{order_id} on your account. Please check the number."
            if order_id is not None
            else "I can't access another customer's information."
        ),
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


def _money(value: object) -> str:
    try:
        return f"${float(str(value)):,.2f}"
    except ValueError:
        return str(value)


def _day(value: object) -> str:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return ""
    return f"{parsed:%b} {parsed.day}, {parsed.year}"


def _order_line(order: dict[str, object]) -> str:
    placed = _day(order.get("created_at"))
    suffix = f", placed {placed}" if placed else ""
    return f"#{order.get('id')}: {order.get('status')}, {_money(order.get('total_amount'))}{suffix}"


def _ticket_line(ticket: dict[str, object]) -> str:
    about = f" about order #{ticket['order_id']}" if ticket.get("order_id") else ""
    category = str(ticket.get("category") or "support").replace("_", " ")
    return f"#{ticket.get('id')} ({category}{about}): {ticket.get('status')}"


def _items(result: dict[str, object] | None) -> list[dict[str, object]]:
    items = result.get("items", []) if result else []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


_REFUND_STATUS_LINES = {
    "requested": "Your refund request for it has been submitted and is waiting for review.",
    "approved": "Your refund request for it has been approved.",
    "processing": "Your refund for it is being processed.",
    "completed": "Your refund for it has been completed.",
    "rejected": "Your refund request for it was declined.",
}


def _tool_message(tool_name: str, result: dict[str, object] | None) -> str:
    """Say what the tool found, with the identifiers the customer needs for a follow-up."""

    result = result or {}
    if tool_name == "get_customer_orders":
        orders = _items(result)
        if not orders:
            return "I couldn't find any orders on your account."
        lines = "; ".join(_order_line(order) for order in orders[:10])
        noun = "order" if len(orders) == 1 else "orders"
        return f"You have {len(orders)} {noun}: {lines}."
    if tool_name == "get_customer_tickets":
        tickets = _items(result)
        if not tickets:
            return "You don't have any support tickets."
        lines = "; ".join(_ticket_line(ticket) for ticket in tickets[:10])
        noun = "support ticket" if len(tickets) == 1 else "support tickets"
        return f"You have {len(tickets)} {noun}: {lines}."
    if tool_name == "get_customer":
        name = result.get("name")
        return f"I found your account{f' ({name})' if name else ''}."
    if tool_name == "get_order" and result.get("id") is not None:
        refund = _REFUND_STATUS_LINES.get(str(result.get("refund_status")))
        refund_line = f" {refund}" if refund else ""
        return f"Order {_order_line(result)}.{refund_line}"
    if tool_name == "get_ticket" and result.get("id") is not None:
        description = str(result.get("description") or "").strip()
        detail = f' "{description[:160]}"' if description else ""
        return f"Ticket {_ticket_line(result)}.{detail}".rstrip()
    if tool_name == "escalate_to_human":
        return "I'll connect you with a human support specialist."
    if tool_name == "create_support_ticket":
        ticket_id = result.get("id")
        return (
            f"I created support ticket #{ticket_id} "
            f"({str(result.get('category') or 'support').replace('_', ' ')}). "
            "Our team will follow up on it."
            if ticket_id is not None
            else "Your support ticket was created."
        )
    if tool_name == "cancel_order" and result.get("order_id") is not None:
        if result.get("changed") is False:
            return f"Order #{result['order_id']} was already {result.get('status')}."
        return f"Order #{result['order_id']} is now cancelled."
    if tool_name == "request_refund" and result.get("order_id") is not None:
        return (
            f"Your refund request for order #{result['order_id']} has been submitted "
            f"(status: {result.get('status')}). We'll let you know once it's reviewed."
        )
    return "The requested business operation completed."


_GREETING = re.compile(
    r"^(?:hi|hello|hey|good (?:morning|afternoon|evening)|merhaba|selam|iyi günler)\b", re.I
)
# "ok bye", "tamam teşekkürler": a short lead-in before the thanks or goodbye.
_CLOSING = re.compile(
    r"^(?:(?:ok(?:ay)?|alright|great|perfect|tamam|peki|süper|harika)[\s,.!]+)?"
    r"(?:thanks|thank you|thx|cheers|that'?s all|bye|goodbye|teşekkür\w*|sağ ?ol\w*|"
    r"görüşürüz|hoşça ?kal)\b",
    re.I,
)
_IDENTITY = re.compile(
    r"\b(?:are you (?:a |an )?(?:bot|robot|human|real person|ai)|who are you|what are you|"
    r"bot mu\w*|robot mu\w*|insan mı\w*|gerçek (?:bir )?insan|sen kimsin|kimsin)\b",
    re.I,
)
# Refusals of a side step (memory declined to store a preference) that a reply answering the
# customer's real question supersedes. System failures are never cleared this way.
_SIDE_STEP_REFUSALS = frozenset(
    {AgentErrorCategory.POLICY_DENIED, AgentErrorCategory.INVALID_TOOL_ARGUMENTS}
)

_RECAP = re.compile(
    r"\b(?:what (?:did|have) we (?:talk(?:ed)? about|discuss(?:ed)?|covered?)|what have you done|"
    r"summar(?:y|ize|ise) (?:of )?(?:our|this) (?:chat|conversation)|ne konuştuk|neler konuştuk|"
    r"neler yaptın|ne yaptın|özetle)\b",
    re.I,
)


def _recap(state: AgentState) -> str:
    """A short recap of what the conversation actually did: lookups, answers, actions.

    Each turn that executed a tool or answered from an article leaves one line in ``covered``;
    greetings, clarifications and "I couldn't find that" replies leave none, and a line said
    twice is listed once.
    """

    covered = list(dict.fromkeys(state.get("covered") or []))
    if covered:
        lines = "\n".join(f"• {line}" for line in covered[-5:])
        return f"Here's what we've covered so far:\n{lines}"
    replies = [
        item["content"].split(". ")[0].rstrip(".")
        for item in state.get("messages", [])[:-1]
        if item["role"] == "assistant" and len(item["content"]) > 20
    ]
    useful = [reply for reply in replies if not reply.startswith(("Hi!", "Merhaba", "Could you"))]
    if not useful:
        return "We've just started. How can I help you today?"
    lines = "\n".join(f"• {reply}." for reply in useful[-4:])
    return f"Here's what we've covered so far:\n{lines}"


# Paraphrases the shipping policy's transit section for "when will it arrive?" on a shipped
# order; the carrier's tracking, not this reply, holds the date.
_TRANSIT_NOTE = (
    "Once an order is with the carrier, delivery time depends on the shipping service and "
    "destination, and the latest tracking event shows the current estimate."
)
_ARRIVAL = re.compile(
    r"\b(?:when (?:will|does|is) .{0,20}(?:arrive|come|deliver|get here)|how long|eta|"
    r"ne zaman (?:gel|ulaş|teslim)|kaç günde)\w*",
    re.I,
)


def _asks_arrival(text: str) -> bool:
    return bool(_ARRIVAL.search(text))


def _shipped(state: AgentState) -> bool:
    result = state.get("tool_result")
    return isinstance(result, dict) and result.get("status") == "shipped"


_MOVE_REPLIES = {
    "greeting": "Hi! How can I help you today?",
    "small_talk": (
        "Thanks for chatting! I'm here to help with your orders, refunds and cancellations, "
        "support tickets, and our store policies. What can I do for you?"
    ),
    "closing": "Thanks for reaching out! Have a great day, and I'm here if you need anything else.",
    "off_topic": (
        "Sorry, I can't help with that here. I can help with your orders, refunds and "
        "cancellations, support tickets, and our store policies. Is there anything along those "
        "lines I can do for you?"
    ),
}


_MOVES = {*_MOVE_REPLIES, "about_assistant", "own_name"}


def _move_reply(state: AgentState) -> str:
    move = str(state.get("conversation_move"))
    if move == "about_assistant":
        return _IDENTITY_REPLY
    if move == "own_name":
        name = state.get("customer_name")
        if name:
            return f"Your account is under the name {name}. How can I help you today?"
        return "I can't see a name on your account here. How can I help you today?"
    return _MOVE_REPLIES[move]


def _covered(state: AgentState, message: str) -> list[str]:
    """This turn's line for the recap, when it actually looked something up or did something."""

    covered = list(state.get("covered") or [])
    grounded = (state.get("answer_grounding") or {}).get("status") == "pass"
    did_something = state.get("tool_execution_status") == "executed" or (
        state.get("knowledge_answer") is not None and grounded
    )
    if did_something and state.get("error_category") is None:
        first = message.split("\n")[0].split(". ")[0].strip().rstrip(".")
        if first:
            covered.append(f"{first}.")
    return covered[-8:]


_IDENTITY_REPLY = (
    "I'm the store's AI assistant. I can check your orders, handle refunds and cancellations, "
    "open support tickets and answer questions about our policies. If you'd like to talk to a "
    "person, I can connect you at any time."
)
# Case-sensitive on purpose: with IGNORECASE, Python matches the dotless "ı" to an English "I".
_TURKISH_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TURKISH_WORDS = re.compile(r"\b(?:merhaba|selam|tesekkur|nasil|lutfen|siparis)", re.I)


_CANCEL_WHICH_ORDER = (
    "I can help cancel your order. Which order would you like to cancel? Share the order "
    "number, or ask me to list your orders."
)


def _latest_user_text(state: AgentState) -> str:
    for item in reversed(state.get("messages", [])):
        if item["role"] == "user":
            return item["content"].strip()
    return ""


_CARD_CANDIDATE = re.compile(r"(?:\d[ -]?){13,19}")


def _shares_card_number(text: str) -> bool:
    """A Luhn-valid 13-19 digit number: a payment card, whatever words surround it."""

    for candidate in _CARD_CANDIDATE.findall(text):
        digits = [int(ch) for ch in candidate if ch.isdigit()]
        if not 13 <= len(digits) <= 19:
            continue
        total = 0
        for index, digit in enumerate(reversed(digits)):
            if index % 2:
                digit *= 2
                digit -= 9 if digit > 9 else 0
            total += digit
        if total % 10 == 0:
            return True
    return False


_REFERENCE_TOOLS = {
    "get_order": "order",
    "cancel_order": "order",
    "request_refund": "order",
    "get_ticket": "ticket",
    "create_support_ticket": "ticket",
}


def _last_reference(state: AgentState) -> dict[str, object] | None:
    """The single order or ticket this reply was about, kept for "what was its number?"."""

    kind = _REFERENCE_TOOLS.get(str(state.get("selected_tool")))
    result = state.get("tool_result") or {}
    if kind is None or state.get("tool_execution_status") != "executed":
        return state.get("last_reference")
    identifier = result.get("order_id") if kind == "order" else None
    identifier = identifier or result.get("id")
    if not isinstance(identifier, int):
        return state.get("last_reference")
    return {"kind": kind, "id": identifier}


def _sorry(state: AgentState) -> str:
    """An apology before the handoff when the customer is plainly upset."""

    # English like the rest of the reply; translation turns the whole reply at once.
    return "I'm sorry for the trouble. " if customer_upset(state, _latest_user_text(state)) else ""


def _open_reply(text: str) -> str:
    """Answer greetings and thanks like a person would; otherwise ask what they need."""

    turkish = bool(_TURKISH_LETTERS.search(text) or _TURKISH_WORDS.search(text))
    if _CLOSING.match(text):
        return (
            "Rica ederim! Başka bir konuda yardımcı olabilirsem buradayım."
            if turkish
            else "You're welcome! I'm here if you need anything else."
        )
    if _GREETING.match(text):
        return (
            "Merhaba! Size nasıl yardımcı olabilirim?"
            if turkish
            else "Hi! How can I help you today?"
        )
    return (
        "Ne yapmak istediğinizi biraz netleştirebilir misiniz? Siparişleriniz, iade ve "
        "iptaller, destek talepleriniz ya da kargo ve politikalarımızla ilgili yardımcı "
        "olabilirim. Başka bir konuysa destek talebi açabilir ya da sizi bir temsilciye "
        "bağlayabilirim."
        if turkish
        else "Could you clarify whether you need help with an order, a refund or cancellation, "
        "a support ticket, or a question about shipping or our policies? If it's something "
        "else, I can open a support ticket or connect you with a person."
    )


def respond(state: AgentState) -> AgentState:
    error_category = state.get("error_category")
    # Set when the reply answers what the customer actually asked (switch language, recap so
    # far) rather than a side step that was refused; that refusal is then not the outcome.
    answered_instead = False
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
    elif state.get("decision_reason") == "customer_withdrew":
        message = "No problem, I've set that aside. Is there anything else I can help with?"
    elif (
        state.get("conversation_move") in _MOVES
        and state.get("request_type") == AgentRequestType.UNCLEAR
        and state.get("tool_execution_status") != "executed"
        and pending_action is None
    ):
        message = _move_reply(state)
    elif (
        state.get("acknowledgement")
        and state.get("tool_execution_status") != "executed"
        and (pending_action is None or pending_action.status != PendingActionStatus.PENDING)
        and not state.get("workflow_active")
    ):
        message = "Great! Is there anything else I can help with?"
    elif state.get("language_requested") in {"english", "turkish"}:
        message = (
            "Elbette, bundan sonra Türkçe yazacağım."
            if state.get("language_requested") == "turkish"
            else "Sure, I'll reply in English from now on."
        )
        answered_instead = True
    elif state.get("handoff_offered") and error_category is None:
        message = HANDOFF_OFFER_MESSAGE
    elif _RECAP.search(_latest_user_text(state)) and state.get("tool_execution_status") != (
        "executed"
    ):
        message = _recap(state)
        answered_instead = True
    elif (
        _IDENTITY.search(_latest_user_text(state))
        and state.get("tool_execution_status") != "executed"
        and state.get("pending_action") is None
    ):
        message = _IDENTITY_REPLY
    elif _suspended_retrieval_unavailable(state):
        message = _unavailable_suspended_workflow_message(state)
    elif state.get("write_outcome_unknown"):
        message = (
            "I couldn't confirm whether the action completed, so I won't repeat it automatically."
        )
    elif (
        _has_suspended_mutation(state)
        and state.get("tool_result") is not None
        and tool_name
        and error_category is None
        and state.get("workflow_interruption_status") == "suspended"
    ):
        # A read answered while a write waits ("is order 5 cancelled now?"): give the
        # answer, then say the saved request is still waiting.
        label = _suspended_workflow_label(state)
        message = (
            f"{_tool_message(tool_name, state.get('tool_result'))} Your {label} is still "
            "saved and waiting for your confirmation."
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
        message = _CANCEL_WHICH_ORDER
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
        message = (
            "I can check that for you. Which order do you mean? Share the order number, "
            "or ask me to list your orders."
        )
    elif not tool_name and state.get("intent") == Intent.TICKET_LOOKUP:
        message = (
            "Which ticket do you mean? Share the ticket number, or ask me to list your "
            "support tickets."
        )
    elif not tool_name and state.get("intent") == Intent.ORDER_CANCEL:
        message = _CANCEL_WHICH_ORDER
    elif state.get("intent") == Intent.HUMAN_ESCALATION and (
        clarification_required
        or (state.get("request_type") == AgentRequestType.ESCALATION and not tool_name)
    ):
        message = (
            "I can help connect you with a support specialist. Could you tell me the reason "
            "you would like to speak with someone?"
        )
    elif not tool_name and state.get("intent") == Intent.HUMAN_ESCALATION:
        message = f"{_sorry(state)}I'll connect you with a human support specialist."
    elif state.get("request_type") == AgentRequestType.UNCLEAR and state.get("asks_memory_summary"):
        # Neither model saw a business request, and the reader saw "what do you know about me".
        message = _memory_summary_message(state)
    elif state.get("request_type") == AgentRequestType.UNCLEAR:
        message = _open_reply(_latest_user_text(state))
    elif tool_name:
        message = _tool_message(tool_name, state.get("tool_result"))
        if tool_name == "escalate_to_human":
            message = f"{_sorry(state)}{message}"
        if _asks_arrival(_latest_user_text(state)) and _shipped(state):
            message += f" {_TRANSIT_NOTE}"
    else:
        message = _OUT_OF_SCOPE
    message = _apply_memory_context(state, message)
    if state.get("customer_upset") is True and not message.startswith("I'm sorry"):
        # Read by the model, never guessed from keywords: an upset customer hears it first.
        message = f"I'm sorry for the trouble. {message}"
    if _shares_card_number(_latest_user_text(state)):
        message += (
            " For your security, please don't share card numbers here; I never need them to "
            "help you."
        )
    result: AgentState = {
        "covered": _covered(state, message),
        "last_reference": _last_reference(state),
        "final_response": message,
        "messages": [{"role": "assistant", "content": message}],
        "workflow_state": _response_workflow_state(state),
    }
    if answered_instead and error_category in _SIDE_STEP_REFUSALS:
        result["error_category"] = None
        result["last_error"] = None
    return result


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
    order_id = action.arguments.get("order_id")
    for_order = f" for order #{order_id}" if isinstance(order_id, int | str) and order_id else ""
    if action.tool_name == "request_refund":
        return (
            f"I can submit your refund request{for_order}. Before I continue, I need your "
            "confirmation. Would you like me to proceed?"
        )
    if action.tool_name == "cancel_order":
        return (
            f"I can submit your cancellation request{for_order}. Before I continue, I need your "
            "confirmation. Would you like me to proceed?"
        )
    return (
        "I can submit this request. Before I continue, I need your confirmation. "
        "Would you like me to proceed?"
    )


# What the customer reads when the help articles do not cover the question. The grounded
# generator's own wording is for operators and audits.
_NO_ARTICLE_REPLY = (
    "I couldn't find an answer to that in our help articles. I can open a support ticket so "
    "our team can follow up, or connect you with a person."
)


def _customer_knowledge_answer(state: AgentState) -> str:
    answer = state.get("knowledge_answer") or ""
    if SAFE_UNCERTAINTY in answer:
        remainder = answer.replace(SAFE_UNCERTAINTY, "").strip()
        answer = f"{remainder} {_NO_ARTICLE_REPLY}".strip() if remainder else _NO_ARTICLE_REPLY
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
