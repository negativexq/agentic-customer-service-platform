"""Operator workspace: customer context, internal notes, saved replies, service levels and CSAT.

Notes and saved replies are operator data. They are DLP-redacted like transcript text, never
shown to the customer, and never passed to the agent.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now_naive
from app.memory.dlp import redact_message_text
from app.models.entities import (
    Conversation,
    ConversationMessage,
    ConversationOwner,
    ConversationOwnershipEvent,
    Escalation,
    EscalationPriority,
    MessageSender,
)
from app.models.workspace import ConversationFeedback, ConversationNote, SavedReply, SlaTarget
from app.ui.workspace_schemas import SlaStatusView

DEFAULT_SLA_MINUTES: dict[str, int] = {
    EscalationPriority.URGENT: 15,
    EscalationPriority.HIGH: 60,
    EscalationPriority.MEDIUM: 240,
    EscalationPriority.LOW: 1440,
}
AT_RISK_SHARE = 0.25


def sla_targets(session: Session, tenant_id: str) -> dict[str, int]:
    targets = dict(DEFAULT_SLA_MINUTES)
    for row in session.scalars(select(SlaTarget).where(SlaTarget.tenant_id == tenant_id)):
        targets[row.priority] = row.first_response_minutes
    return targets


def save_sla_targets(
    session: Session, tenant_id: str, actor_id: str, targets: dict[str, int]
) -> dict[str, int]:
    existing = {
        row.priority: row
        for row in session.scalars(select(SlaTarget).where(SlaTarget.tenant_id == tenant_id))
    }
    now = utc_now_naive()
    for priority, minutes in targets.items():
        row = existing.get(priority)
        if row is None:
            session.add(
                SlaTarget(
                    tenant_id=tenant_id,
                    priority=priority,
                    first_response_minutes=minutes,
                    updated_by=actor_id,
                    updated_at=now,
                )
            )
        else:
            row.first_response_minutes = minutes
            row.updated_by = actor_id
            row.updated_at = now
    session.commit()
    return sla_targets(session, tenant_id)


def sla_statuses(
    session: Session,
    conversations: Sequence[Conversation],
    escalations: dict[int, Escalation],
    targets: dict[str, int],
    now: datetime | None = None,
) -> dict[int, SlaStatusView]:
    """How long each customer has waited for a person, against the first-response target.

    A customer waits from the escalation (or their first unanswered message after an operator
    reply) until an operator answers. Conversations the AI is handling have no SLA.
    """

    waiting = [
        c for c in conversations if c.owner in (ConversationOwner.QUEUED, ConversationOwner.HUMAN)
    ]
    if not waiting:
        return {}
    now = now or utc_now_naive()
    pks = [c.id for c in waiting]
    rows = session.execute(
        select(
            ConversationMessage.conversation_pk,
            ConversationMessage.sender_type,
            ConversationMessage.created_at,
        )
        .where(
            ConversationMessage.conversation_pk.in_(pks),
            ConversationMessage.sender_type.in_([MessageSender.CUSTOMER, MessageSender.OPERATOR]),
        )
        .order_by(ConversationMessage.conversation_pk, ConversationMessage.sequence)
    )
    last_operator: dict[int, datetime] = {}
    first_unanswered: dict[int, datetime | None] = {}
    for pk, sender, created_at in rows:
        if sender == MessageSender.OPERATOR:
            last_operator[pk] = created_at
            first_unanswered[pk] = None
        elif first_unanswered.get(pk) is None:
            first_unanswered[pk] = created_at

    result: dict[int, SlaStatusView] = {}
    for conversation in waiting:
        escalation = escalations.get(conversation.id)
        escalated_at = escalation.created_at if escalation is not None else None
        since = first_unanswered.get(conversation.id)
        if conversation.id not in last_operator and escalated_at is not None:
            # Nobody has answered since the hand-off: the clock starts at the escalation.
            since = escalated_at if since is None else min(since, escalated_at)
        if since is None:
            continue
        priority = str(escalation.priority) if escalation is not None else EscalationPriority.MEDIUM
        minutes = targets.get(priority, DEFAULT_SLA_MINUTES[EscalationPriority.MEDIUM])
        due = since + timedelta(minutes=minutes)
        remaining = (due - now).total_seconds()
        state = (
            "breached"
            if remaining < 0
            else "at_risk"
            if remaining < minutes * 60 * AT_RISK_SHARE
            else "ok"
        )
        result[conversation.id] = SlaStatusView(
            priority=priority, target_minutes=minutes, waiting_since=since, due_at=due, state=state
        )
    return result


def list_notes(session: Session, conversation_pk: int) -> list[ConversationNote]:
    return list(
        session.scalars(
            select(ConversationNote)
            .where(
                ConversationNote.conversation_pk == conversation_pk,
                ConversationNote.expires_at > utc_now_naive(),
            )
            .order_by(ConversationNote.id)
        )
    )


def add_note(
    session: Session, conversation: Conversation, *, actor_id: str, author_name: str, content: str
) -> ConversationNote:
    text, redacted = redact_message_text(content)
    now = utc_now_naive()
    note = ConversationNote(
        tenant_id=conversation.tenant_id,
        conversation_pk=conversation.id,
        author_actor_id=actor_id,
        author_name=author_name,
        content=text,
        redacted=redacted,
        created_at=now,
        expires_at=now + timedelta(days=get_settings().conversation_message_ttl_days),
    )
    session.add(note)
    session.commit()
    return note


def list_saved_replies(session: Session, tenant_id: str) -> list[SavedReply]:
    return list(
        session.scalars(
            select(SavedReply)
            .where(SavedReply.tenant_id == tenant_id)
            .order_by(SavedReply.use_count.desc(), SavedReply.title)
        )
    )


def resolved_by(session: Session, conversation: Conversation) -> str:
    """'human' when a person ever took the conversation over, otherwise 'ai'."""

    took_over = session.scalar(
        select(func.count())
        .select_from(ConversationOwnershipEvent)
        .where(
            ConversationOwnershipEvent.conversation_pk == conversation.id,
            ConversationOwnershipEvent.to_owner == ConversationOwner.HUMAN,
        )
    )
    return "human" if took_over else "ai"


def submit_feedback(
    session: Session, conversation: Conversation, *, rating: int, comment: str
) -> ConversationFeedback:
    text, _ = redact_message_text(comment)
    now = utc_now_naive()
    row = session.scalar(
        select(ConversationFeedback).where(ConversationFeedback.conversation_pk == conversation.id)
    )
    if row is None:
        row = ConversationFeedback(
            tenant_id=conversation.tenant_id,
            conversation_pk=conversation.id,
            customer_id=conversation.customer_id,
            created_at=now,
        )
        session.add(row)
    row.rating = rating
    row.comment = text
    row.resolved_by = resolved_by(session, conversation)
    row.updated_at = now
    session.commit()
    return row


def feedback_for(session: Session, pks: Sequence[int]) -> dict[int, ConversationFeedback]:
    if not pks:
        return {}
    rows = session.scalars(
        select(ConversationFeedback).where(ConversationFeedback.conversation_pk.in_(pks))
    )
    return {row.conversation_pk: row for row in rows}
