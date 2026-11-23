"""Audit trail: who did what to which conversation, and when, in one ordered list.

Everything here is read from records the platform already writes as it works (ownership
transitions, the assistant's run projections, operator messages and notes). Nothing is
inferred from message text, and no customer message content is exposed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import TypedDict

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.models.entities import (
    Conversation,
    ConversationMessage,
    ConversationOwnershipEvent,
    Customer,
    Escalation,
)
from app.models.workspace import ConversationNote
from app.ui.audit_schemas import AuditActor, AuditEvent, AuditKind, AuditLogView
from app.ui.outcome import RUN_BLOCKED, RUN_DECLINED, RUN_ERROR
from app.ui.schemas import AgentRunView

ASSISTANT = AuditActor(type="ai", id=None, name="AI assistant")
SYSTEM = AuditActor(type="system", id=None, name="System")

# Ownership reason codes the conversation service records, read as one plain sentence each.
_OWNERSHIP: dict[str, tuple[str, str, AuditKind]] = {
    "customer_escalation": (
        "handoff.queued",
        "Handed the conversation to the team queue",
        "handoff",
    ),
    "operator_takeover": (
        "operator.took_over",
        "Took over the conversation from the AI assistant",
        "handoff",
    ),
    "operator_claimed": (
        "operator.claimed",
        "Picked up the conversation from the queue",
        "handoff",
    ),
    "operator_released": (
        "operator.released",
        "Handed the conversation back to the AI assistant",
        "handoff",
    ),
    "operator_resolved": ("conversation.closed", "Closed the conversation", "conversation"),
    "customer_reopened": (
        "conversation.reopened",
        "Reopened the conversation by writing again",
        "conversation",
    ),
}

# Decision reasons recorded on run projections, in words an operator or auditor can read.
_REASONS = {
    "Current business state does not permit the requested action.": (
        "The order's current state does not allow this"
    ),
    "Duplicate business action was prevented.": "Already requested, so it was not done twice",
    "Referenced business resource was not found.": "The order or record was not found",
    "cross_customer_access_attempt": "Asked for another customer's information",
    "instruction_override_attempt": "Tried to override the assistant's safety rules",
    "memory_security_override_attempt": "Tried to change the assistant's rules through memory",
    "Policy verification denied execution authority.": "Policy does not allow this action",
    "idempotency_replay_prevented": "A repeated request was not carried out again",
}
_FAILURES = {
    "llm_timeout": "The language model took too long to answer",
    "llm_unavailable": "The language model could not be reached",
    "llm_malformed_output": "The language model's answer could not be read",
    "database_transient": "The database briefly refused a request",
    "database_unavailable": "The database could not be reached",
    "tool_timeout": "An action took too long to finish",
    "tool_transient_failure": "An action failed and may work on a retry",
    "tool_permanent_failure": "An action failed",
    "retrieval_timeout": "The knowledge search took too long",
    "retrieval_unavailable": "The knowledge search could not be reached",
    "memory_failure": "Reading or saving customer memory failed",
    "circuit_open": "Model calls were paused after repeated failures",
    "rate_limited": "The model provider limited the request rate",
}
# Business actions that change something for the customer, as the sentence the log shows.
_WRITES = {
    "request_refund": "Requested a refund",
    "cancel_order": "Cancelled an order",
    "create_support_ticket": "Opened a support ticket",
}
_ASKED = {
    "request_refund": "a refund",
    "cancel_order": "an order cancellation",
    "create_support_ticket": "a support ticket",
}


class _ConversationFields(TypedDict):
    conversation_pk: int
    conversation_id: str
    customer_id: int
    customer_name: str


def _words(value: str) -> str:
    text = value.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def _reason(run: AgentRunView) -> str | None:
    if run.failure_category:
        return _FAILURES.get(run.failure_category, _words(run.failure_category))
    reason = run.decision_reason
    if not reason:
        return None
    if reason == "Policy verification denied execution authority." and "memory_action" in run.path:
        return "Not saved: the customer did not explicitly ask to remember it"
    return _REASONS.get(reason, reason)


def _run_events(run: AgentRunView, conversation: Conversation, customer: str) -> list[AuditEvent]:
    def event(action: str, kind: AuditKind, summary: str, detail: str | None = None) -> AuditEvent:
        return AuditEvent(
            id=f"run:{run.run_id}:{action}",
            at=run.started_at,
            kind=kind,
            action=action,
            actor=ASSISTANT,
            conversation_pk=conversation.id,
            conversation_id=conversation.conversation_id,
            customer_id=conversation.customer_id,
            customer_name=customer,
            summary=summary,
            detail=detail,
            run_id=run.run_id,
        )

    topic = _words(run.intent) if run.intent not in {"", "unknown"} else "an unrecognised request"
    if run.status == RUN_ERROR:
        return [
            event(
                "assistant.failed",
                "assistant",
                f"Could not finish a reply about {topic.lower()}",
                _reason(run),
            )
        ]
    if run.status == RUN_BLOCKED:
        return [event("assistant.blocked", "safety", "Blocked a request", _reason(run))]
    if run.status == RUN_DECLINED:
        return [
            event(
                "assistant.declined",
                "assistant",
                f"Declined a request about {topic.lower()}",
                _reason(run),
            )
        ]
    if run.status == "waiting_confirmation":
        tool = next((t.name for t in run.tools if t.name in _ASKED), None)
        what = _ASKED.get(tool or "", "an action")
        return [
            event("assistant.confirmation", "assistant", f"Asked the customer to confirm {what}")
        ]
    events = [
        event("action.executed", "action", _WRITES[tool.name], f"Run {run.run_id[:8]}")
        for tool in run.tools
        if tool.name in _WRITES and tool.status == "completed"
    ]
    escalated = any(t.name == "escalate_to_human" and t.status == "completed" for t in run.tools)
    if not events and not escalated and run.intent == "human_escalation":
        # The handoff itself is an ownership event; a run that did not hand over asked first.
        events.append(
            event(
                "assistant.clarified",
                "assistant",
                "Asked what the customer needs before handing over to the team",
            )
        )
    elif not events and not escalated:
        summary = (
            f"Answered a question about {topic.lower()}"
            if run.intent not in {"", "unknown"}
            else "Replied to a general message"
        )
        events.append(event("assistant.answered", "assistant", summary))
    return events


def _haystack(event: AuditEvent) -> str:
    """The text an auditor searches by: who, which customer and conversation, and what happened."""

    parts = [
        event.actor.name,
        event.actor.id or "",
        event.customer_name,
        event.conversation_id,
        event.summary,
        event.detail or "",
        event.run_id or "",
    ]
    return " ".join(parts).lower()


def audit_log(
    session: Session,
    tenant_id: str,
    runs: Sequence[AgentRunView],
    *,
    since: datetime,
    customer_ids: set[int] | None = None,
    operator_names: dict[str, str] | None = None,
    conversation_pk: int | None = None,
    kinds: Iterable[str] | None = None,
    search: str | None = None,
    limit: int = 500,
) -> AuditLogView:
    """Every recorded step on conversations active since ``since``, newest first."""

    query = select(Conversation).where(Conversation.tenant_id == tenant_id)
    if conversation_pk is not None:
        query = query.where(Conversation.id == conversation_pk)
    else:
        query = query.where(Conversation.updated_at >= since)
    if customer_ids is not None:
        query = query.where(Conversation.customer_id.in_(customer_ids))
    conversations = {item.id: item for item in session.scalars(query)}
    by_public_id = {item.conversation_id: item for item in conversations.values()}
    customers = {
        item.id: item.name
        for item in session.scalars(
            select(Customer).where(Customer.id.in_({c.customer_id for c in conversations.values()}))
        )
    }
    pks = set(conversations)
    names = dict(operator_names or {})
    for actor_id, name in session.execute(
        select(ConversationMessage.sender_actor_id, ConversationMessage.sender_display_name).where(
            ConversationMessage.conversation_pk.in_(pks),
            ConversationMessage.sender_type == "operator",
            ConversationMessage.sender_actor_id.is_not(None),
        )
    ):
        if actor_id and name:
            names.setdefault(actor_id, name)

    def operator(actor_id: str) -> AuditActor:
        return AuditActor(type="operator", id=actor_id, name=names.get(actor_id, "Operator"))

    def base(conversation: Conversation) -> _ConversationFields:
        return {
            "conversation_pk": conversation.id,
            "conversation_id": conversation.conversation_id,
            "customer_id": conversation.customer_id,
            "customer_name": customers.get(
                conversation.customer_id, f"Customer {conversation.customer_id}"
            ),
        }

    events: list[AuditEvent] = []
    for conversation in conversations.values():
        if conversation_pk is not None or conversation.created_at >= since:
            events.append(
                AuditEvent(
                    id=f"conversation:{conversation.id}:started",
                    at=conversation.created_at,
                    kind="conversation",
                    action="conversation.started",
                    actor=AuditActor(
                        type="customer",
                        id=None,
                        name=customers.get(conversation.customer_id, "Customer"),
                    ),
                    summary="Started a conversation; the AI assistant is answering",
                    **base(conversation),
                )
            )

    escalations: dict[int, list[Escalation]] = {}
    for escalation in session.scalars(
        select(Escalation)
        .where(Escalation.conversation_pk.in_(pks))
        .order_by(Escalation.created_at)
    ):
        escalations.setdefault(escalation.conversation_pk or 0, []).append(escalation)

    for record in session.scalars(
        select(ConversationOwnershipEvent)
        .where(ConversationOwnershipEvent.conversation_pk.in_(pks))
        .order_by(ConversationOwnershipEvent.created_at)
    ):
        if conversation_pk is None and record.created_at < since:
            continue
        conversation = conversations[record.conversation_pk]
        action, summary, kind = _OWNERSHIP.get(
            record.reason_code,
            (
                "ownership.changed",
                f"Moved the conversation from {record.from_owner} to {record.to_owner}",
                "handoff",
            ),
        )
        detail = None
        if record.reason_code == "customer_escalation":
            actor = ASSISTANT
            match = next(
                (
                    e
                    for e in escalations.get(record.conversation_pk, [])
                    if e.created_at <= record.created_at
                ),
                None,
            )
            if match is not None:
                detail = f"{_words(match.priority)} priority · {match.reason}"
        elif record.reason_code == "customer_reopened":
            actor = AuditActor(
                type="customer", id=None, name=str(base(conversation)["customer_name"])
            )
        else:
            actor = operator(record.actor_id)
        events.append(
            AuditEvent(
                id=f"ownership:{record.id}",
                at=record.created_at,
                kind=kind,
                action=action,
                actor=actor,
                summary=summary,
                detail=detail,
                **base(conversation),
            )
        )

    for message in session.scalars(
        select(ConversationMessage).where(
            ConversationMessage.conversation_pk.in_(pks),
            ConversationMessage.sender_type == "operator",
            ConversationMessage.created_at >= since if conversation_pk is None else true(),
        )
    ):
        conversation = conversations[message.conversation_pk]
        events.append(
            AuditEvent(
                id=f"message:{message.id}",
                at=message.created_at,
                kind="operator",
                action="operator.replied",
                actor=operator(message.sender_actor_id or "operator"),
                summary="Replied to the customer",
                **base(conversation),
            )
        )

    for note in session.scalars(
        select(ConversationNote).where(
            ConversationNote.conversation_pk.in_(pks),
            ConversationNote.created_at >= since if conversation_pk is None else true(),
        )
    ):
        conversation = conversations[note.conversation_pk]
        names.setdefault(note.author_actor_id, note.author_name)
        events.append(
            AuditEvent(
                id=f"note:{note.id}",
                at=note.created_at,
                kind="operator",
                action="note.added",
                actor=operator(note.author_actor_id),
                summary="Added an internal note",
                **base(conversation),
            )
        )

    for run in runs:
        run_conversation = by_public_id.get(run.conversation_id)
        if run_conversation is None or run.operation_type != "agent_request":
            continue
        events.extend(_run_events(run, run_conversation, base(run_conversation)["customer_name"]))

    wanted = set(kinds or [])
    if wanted:
        events = [item for item in events if item.kind in wanted]
    needle = (search or "").strip().lower()
    if needle:
        events = [item for item in events if needle in _haystack(item)]
    events.sort(key=lambda item: (item.at, item.id), reverse=True)
    counts: dict[str, int] = {}
    for item in events:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return AuditLogView(total=len(events), counts=counts, events=events[:limit])
