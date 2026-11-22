"""Human-readable map of the workflows the runtime executes for each intent.

The runtime's routing, compiler and policy remain the source of truth; this registry describes
them so operators can see what happens for each kind of request. Keep it in sync with
``app.agent.decision_compiler`` when a workflow changes.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.agent.compiler_contracts import CompilerReasonCode
from app.agent_config.schemas import ActionMode, AgentConfig
from app.ui.schemas import AgentRunView
from app.ui.studio_schemas import ProcedureStep, ProcedureView

Step = tuple[str, str, str, str | None]


@dataclass(frozen=True)
class ProcedureDefinition:
    intent: str
    name: str
    summary: str
    request_type: str
    steps: tuple[Step, ...]
    knowledge: tuple[str, ...] = field(default_factory=tuple)

    @property
    def actions(self) -> list[str]:
        return [step[3] for step in self.steps if step[3] is not None]


_UNDERSTAND: Step = (
    "understand",
    "Understand the request",
    "The model proposes an intent and the entities the customer mentioned. It cannot pick tools.",
    None,
)


def _knowledge(intent: str, name: str, document: str, summary: str) -> ProcedureDefinition:
    return ProcedureDefinition(
        intent=intent,
        name=name,
        summary=summary,
        request_type="knowledge_only",
        knowledge=(document,),
        steps=(
            _UNDERSTAND,
            (
                "knowledge",
                "Retrieve policy",
                f"Search the knowledge base, mainly {document}.",
                None,
            ),
            (
                "respond",
                "Grounded answer",
                "Answer only from retrieved passages and cite them. Without evidence, say so.",
                None,
            ),
        ),
    )


def _lookup(intent: str, name: str, collect: str, action: str, summary: str) -> ProcedureDefinition:
    steps: list[Step] = [_UNDERSTAND]
    if collect:
        steps.append(("collect", "Identify the target", collect, None))
    steps += [
        ("policy", "Ownership check", "The record must belong to the signed-in customer.", None),
        ("lookup", "Read the record", f"Call {action}.", action),
        ("respond", "Answer", "Summarise the record in plain language.", None),
    ]
    return ProcedureDefinition(intent, name, summary, "read_action", tuple(steps))


PROCEDURES: tuple[ProcedureDefinition, ...] = (
    _lookup(
        "order_lookup",
        "Order status",
        "Use the order number the customer gave, or their latest order when they say so.",
        "get_order",
        "Tell the customer where an order is and what state it is in.",
    ),
    _lookup("order_list", "List orders", "", "get_customer_orders", "Show the customer's orders."),
    _lookup(
        "ticket_lookup",
        "Ticket status",
        "Use the ticket number the customer gave; ask for it when missing.",
        "get_ticket",
        "Report on one support ticket.",
    ),
    _lookup(
        "ticket_list", "List tickets", "", "get_customer_tickets", "Show the customer's tickets."
    ),
    _lookup(
        "customer_lookup", "Account details", "", "get_customer", "Read the customer's profile."
    ),
    ProcedureDefinition(
        "ticket_create",
        "Open a support ticket",
        "Record an issue the assistant cannot solve directly.",
        "write_action",
        (
            _UNDERSTAND,
            (
                "collect",
                "Collect details",
                "Ask for a category and a description if missing.",
                None,
            ),
            ("policy", "Policy", "Low-risk write: runs without confirmation by default.", None),
            ("act", "Create the ticket", "Call create_support_ticket.", "create_support_ticket"),
            ("respond", "Confirm", "Share the new ticket number.", None),
        ),
    ),
    ProcedureDefinition(
        "order_cancel",
        "Cancel an order",
        "Cancel an order that has not shipped yet.",
        "write_action",
        (
            _UNDERSTAND,
            ("collect", "Identify the order", "An explicit order number is required.", None),
            (
                "policy",
                "Validate the target",
                "The order must exist, belong to the customer and still be cancellable.",
                None,
            ),
            (
                "confirm",
                "Ask for confirmation",
                "The customer must confirm explicitly; the action is revalidated before it runs.",
                None,
            ),
            ("act", "Cancel", "Call cancel_order once (idempotent).", "cancel_order"),
            ("respond", "Confirm", "Tell the customer the order is cancelled.", None),
        ),
        knowledge=("cancellation-policy",),
    ),
    ProcedureDefinition(
        "refund_request",
        "Request a refund",
        "Start a refund for a delivered order with a supported reason.",
        "write_action",
        (
            _UNDERSTAND,
            (
                "collect",
                "Order and reason",
                "Needs the order number and the customer's own reason (for example damaged).",
                None,
            ),
            (
                "policy",
                "Validate",
                "The order must be delivered and owned by the customer; the reason must be "
                "supported.",
                None,
            ),
            (
                "confirm",
                "Ask for confirmation",
                "Explicit confirmation, then revalidation before execution.",
                None,
            ),
            ("act", "Request refund", "Call request_refund once (idempotent).", "request_refund"),
            ("respond", "Confirm", "Share the refund request status and next steps.", None),
        ),
        knowledge=("refund-policy",),
    ),
    ProcedureDefinition(
        "human_escalation",
        "Hand off to a person",
        "Queue the conversation for an operator when the customer asks or the case needs it.",
        "escalation",
        (
            _UNDERSTAND,
            ("collect", "Summarise", "Capture the reason, a priority and a short summary.", None),
            ("act", "Escalate", "Call escalate_to_human.", "escalate_to_human"),
            (
                "handoff",
                "Queue for an operator",
                "The AI stops replying; the conversation waits in the inbox until someone takes "
                "over.",
                None,
            ),
        ),
    ),
    _knowledge(
        "refund_policy",
        "Refund policy questions",
        "refund-policy",
        "Explain refund rules without promising an outcome.",
    ),
    _knowledge(
        "refund_eligibility",
        "Refund eligibility",
        "refund-policy",
        "Explain whether refunds generally apply; the order itself decides.",
    ),
    _knowledge(
        "cancellation_policy",
        "Cancellation policy questions",
        "cancellation-policy",
        "Explain when orders can be cancelled.",
    ),
    _knowledge(
        "cancellation_explanation",
        "Why an order was cancelled",
        "cancellation-policy",
        "Explain cancellation reasons in general terms.",
    ),
    _knowledge(
        "shipping_policy",
        "Shipping questions",
        "shipping-policy",
        "Answer delivery time and shipping questions.",
    ),
    _knowledge(
        "support_faq", "General FAQ", "support-faq", "Answer common account and support questions."
    ),
    ProcedureDefinition(
        "memory_remember",
        "Remember a preference",
        "Store a preference the customer explicitly asks the assistant to remember.",
        "memory_action",
        (
            _UNDERSTAND,
            (
                "policy",
                "Memory policy",
                "Only explicit, non-sensitive preferences are stored; values are masked by DLP.",
                None,
            ),
            ("respond", "Confirm", "Confirm what was remembered.", None),
        ),
    ),
    ProcedureDefinition(
        "memory_forget",
        "Forget a preference",
        "Delete a stored preference on request.",
        "memory_action",
        (
            _UNDERSTAND,
            ("policy", "Memory policy", "Delete only the customer's own memory.", None),
            ("respond", "Confirm", "Confirm what was forgotten.", None),
        ),
    ),
    ProcedureDefinition(
        "capability_question",
        "What can you do?",
        "Describe what the assistant can and cannot help with.",
        "informational",
        (_UNDERSTAND, ("respond", "Describe capabilities", "A fixed, accurate list.", None)),
    ),
)

_CLARIFY = {
    CompilerReasonCode.ORDER_REQUIRED.value,
    CompilerReasonCode.ORDER_UNRESOLVED.value,
    CompilerReasonCode.TARGET_REQUIRED.value,
    CompilerReasonCode.TICKET_REQUIRED.value,
    CompilerReasonCode.REFUND_REASON_REQUIRED.value,
    CompilerReasonCode.TICKET_DETAILS_REQUIRED.value,
    CompilerReasonCode.REQUEST_CLARIFICATION_REQUIRED.value,
    CompilerReasonCode.LATEST_ORDER_NOT_GROUNDED.value,
    CompilerReasonCode.KNOWLEDGE_QUERY_REQUIRED.value,
}


def is_clarification(run: AgentRunView) -> bool:
    return run.evidence.compiler.reason_code.value in _CLARIFY or run.status == "needs_input"


def is_failed(run: AgentRunView) -> bool:
    return bool(run.failure_category) or run.status == "error"


def _configured_steps(definition: ProcedureDefinition, config: AgentConfig) -> list[ProcedureStep]:
    """Default steps, adjusted for actions the team switched off or made ask first."""

    steps: list[ProcedureStep] = []
    has_confirm = any(kind == "confirm" for kind, *_ in definition.steps)
    for kind, title, detail, action in definition.steps:
        mode = config.action_mode(action) if action else ActionMode.ENABLED
        if mode == ActionMode.CONFIRM and not has_confirm:
            steps.append(
                ProcedureStep(
                    kind="confirm",
                    title="Ask the customer first",
                    detail="Your team requires explicit confirmation before this runs.",
                    operator_note="Added by your configuration",
                )
            )
        steps.append(
            ProcedureStep(
                kind=kind,
                title=title,
                detail=detail,
                action=action,
                operator_note="Switched off by your team: the assistant says it cannot do "
                "this and offers a person."
                if mode == ActionMode.DISABLED
                else None,
            )
        )
    return steps


def list_procedures(config: AgentConfig, runs: Sequence[AgentRunView]) -> list[ProcedureView]:
    conversations_escalated = {
        run.conversation_id for run in runs if run.intent == "human_escalation"
    }
    views: list[ProcedureView] = []
    for definition in PROCEDURES:
        matching = [run for run in runs if run.intent == definition.intent]
        views.append(
            ProcedureView(
                intent=definition.intent,
                name=definition.name,
                summary=definition.summary,
                request_type=definition.request_type,
                steps=_configured_steps(definition, config),
                actions=definition.actions,
                knowledge=list(definition.knowledge),
                guidance_count=sum(
                    1
                    for rule in config.active_guidance()
                    if not rule.intents or definition.intent in {i.value for i in rule.intents}
                ),
                usage_runs=len(matching),
                handoff_runs=sum(
                    1
                    for run in matching
                    if run.conversation_id in conversations_escalated
                    and definition.intent != "human_escalation"
                ),
                clarification_runs=sum(1 for run in matching if is_clarification(run)),
                failed_runs=sum(1 for run in matching if is_failed(run)),
            )
        )
    return views
