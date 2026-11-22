"""Recorded conversation transcript.

Every customer, AI, operator, and system message is stored after DLP redaction so operators
can read and follow conversations. Messages are display data only: nothing here feeds the
agent, policy, or execution path, and message text never enters traces, metrics, or logs.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agent.schemas import AgentResponse
from app.core.config import get_settings
from app.core.context import ExecutionContext
from app.core.time import utc_now_naive
from app.memory.dlp import redact_message_text
from app.models.entities import (
    Conversation,
    ConversationMessage,
    MessageRedaction,
    MessageSender,
)

MAX_MESSAGE_CHARS = 4000
_SYSTEM_DECISIONS = {"conversation_handoff_active", "conversation_ownership_changed"}


def append_message(
    session: Session,
    conversation: Conversation,
    *,
    sender: MessageSender,
    content: str,
    sender_actor_id: str | None = None,
    sender_display_name: str | None = None,
    agent_run_id: str | None = None,
    client_message_id: str | None = None,
    now: datetime | None = None,
) -> ConversationMessage:
    """Append under the caller's transaction; the conversation row lock orders sequences."""

    if client_message_id is not None:
        existing = session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_pk == conversation.id,
                ConversationMessage.client_message_id == client_message_id,
            )
        )
        if existing is not None:
            return existing
    created = now or utc_now_naive()
    text, redacted = redact_message_text(content[:MAX_MESSAGE_CHARS])
    sequence = (
        session.scalar(
            select(func.max(ConversationMessage.sequence)).where(
                ConversationMessage.conversation_pk == conversation.id
            )
        )
        or 0
    ) + 1
    message = ConversationMessage(
        tenant_id=conversation.tenant_id,
        conversation_pk=conversation.id,
        customer_id=conversation.customer_id,
        sequence=sequence,
        sender_type=sender,
        sender_actor_id=sender_actor_id,
        sender_display_name=sender_display_name,
        agent_run_id=agent_run_id,
        client_message_id=client_message_id,
        content=text,
        redaction_state=MessageRedaction.REDACTED if redacted else MessageRedaction.NOT_REQUIRED,
        created_at=created,
        expires_at=created + timedelta(days=get_settings().conversation_message_ttl_days),
    )
    session.add(message)
    session.flush()
    return message


def record_chat_turn(
    session: Session, context: ExecutionContext, message: str, response: AgentResponse
) -> None:
    """Record the customer message and the reply the customer actually received."""

    conversation = session.scalar(
        select(Conversation)
        .where(
            Conversation.tenant_id == context.tenant_id,
            Conversation.customer_id == context.effective_customer_id,
            Conversation.conversation_id == context.conversation_id,
        )
        .with_for_update()
    )
    if conversation is None:
        return
    # The escalation reply itself is AI-authored; only withheld or gated replies are system notes.
    silenced = response.decision_reason in _SYSTEM_DECISIONS
    append_message(
        session,
        conversation,
        sender=MessageSender.CUSTOMER,
        content=message,
        agent_run_id=response.agent_run_id,
    )
    append_message(
        session,
        conversation,
        sender=MessageSender.SYSTEM if silenced else MessageSender.AI,
        content=response.message,
        agent_run_id=response.agent_run_id,
    )
    conversation.updated_at = utc_now_naive()
    session.commit()


def list_messages(
    session: Session,
    conversation: Conversation,
    *,
    after_sequence: int = 0,
    limit: int = 500,
    now: datetime | None = None,
) -> list[ConversationMessage]:
    return list(
        session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.conversation_pk == conversation.id,
                ConversationMessage.sequence > after_sequence,
                ConversationMessage.expires_at > (now or utc_now_naive()),
            )
            .order_by(ConversationMessage.sequence)
            .limit(limit)
        )
    )


def last_messages(
    session: Session, conversation_pks: Sequence[int], now: datetime | None = None
) -> dict[int, ConversationMessage]:
    """Latest unexpired message per conversation, for inbox previews."""

    if not conversation_pks:
        return {}
    latest = (
        select(
            ConversationMessage.conversation_pk,
            func.max(ConversationMessage.sequence).label("sequence"),
        )
        .where(
            ConversationMessage.conversation_pk.in_(conversation_pks),
            ConversationMessage.expires_at > (now or utc_now_naive()),
        )
        .group_by(ConversationMessage.conversation_pk)
        .subquery()
    )
    rows = session.scalars(
        select(ConversationMessage).join(
            latest,
            (ConversationMessage.conversation_pk == latest.c.conversation_pk)
            & (ConversationMessage.sequence == latest.c.sequence),
        )
    )
    return {row.conversation_pk: row for row in rows}


def delete_customer_messages(session: Session, *, tenant_id: str, customer_id: int) -> int:
    result = session.execute(
        delete(ConversationMessage).where(
            ConversationMessage.tenant_id == tenant_id,
            ConversationMessage.customer_id == customer_id,
        )
    )
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def purge_expired_messages(session: Session, now: datetime | None = None) -> int:
    result = session.execute(
        delete(ConversationMessage).where(
            ConversationMessage.expires_at <= (now or utc_now_naive())
        )
    )
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)
