"""Deterministic conversation ownership; the LLM can never select a transition."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.context import ExecutionContext
from app.core.time import utc_now_naive
from app.models.entities import (
    Conversation,
    ConversationOwner,
    ConversationOwnershipEvent,
    Escalation,
    EscalationStatus,
)
from app.tools.base import InvalidStateTransitionError

_ACTIVE_ESCALATION_STATUSES = (EscalationStatus.QUEUED, EscalationStatus.ASSIGNED)


class ConversationOwnershipChangedError(InvalidStateTransitionError):
    """The caller acted on a conversation version that is no longer current."""

    def __init__(self) -> None:
        super().__init__("Conversation ownership changed during this operation.")


@dataclass(frozen=True, slots=True)
class AgentTurn:
    """Ownership observed when an agent turn is admitted."""

    owner: ConversationOwner
    version: int
    ai_resumed_at: datetime | None

    @property
    def ai_may_respond(self) -> bool:
        return self.owner == ConversationOwner.AI


def begin_agent_turn(session: Session, context: ExecutionContext) -> AgentTurn:
    """Create or load the conversation, reopening it to the AI if it was closed."""

    conversation = _locked_conversation(session, context) or _create_conversation(session, context)
    if conversation.owner == ConversationOwner.CLOSED:
        now = utc_now_naive()
        _transition(
            session,
            conversation,
            to_owner=ConversationOwner.AI,
            actor_id=context.principal.actor_id,
            reason_code="customer_reopened",
            now=now,
        )
        conversation.ai_resumed_at = now
    turn = AgentTurn(
        owner=ConversationOwner(conversation.owner),
        version=conversation.ownership_version,
        ai_resumed_at=conversation.ai_resumed_at,
    )
    # End the transaction so the row lock is never held across an LLM call.
    session.commit()
    return turn


def lock_current_agent_turn(
    session: Session, context: ExecutionContext, expected_version: int
) -> Conversation:
    """Hold ownership stable until the caller's business write commits or rolls back."""

    conversation = _locked_conversation(session, context)
    if (
        conversation is None
        or conversation.owner != ConversationOwner.AI
        or conversation.ownership_version != expected_version
    ):
        raise ConversationOwnershipChangedError()
    return conversation


def queue_for_human(
    session: Session,
    conversation: Conversation,
    *,
    escalation_id: int,
    actor_id: str,
) -> None:
    """Silence the AI in the same transaction that created the escalation."""

    escalation = session.get(Escalation, escalation_id)
    # An idempotent replay can return an escalation that has already been resolved.
    if escalation is None or escalation.status != EscalationStatus.QUEUED:
        return
    escalation.conversation_pk = conversation.id
    _transition(
        session,
        conversation,
        to_owner=ConversationOwner.QUEUED,
        actor_id=actor_id,
        reason_code="customer_escalation",
        now=utc_now_naive(),
    )


def claim_conversation(
    session: Session,
    *,
    tenant_id: str,
    conversation_pk: int,
    actor_id: str,
    expected_version: int,
) -> Conversation:
    conversation = _locked_for_transition(session, tenant_id, conversation_pk, expected_version)
    if conversation.owner != ConversationOwner.QUEUED:
        raise ConversationOwnershipChangedError()
    now = utc_now_naive()
    conversation.assignee_actor_id = actor_id
    _transition(
        session,
        conversation,
        to_owner=ConversationOwner.HUMAN,
        actor_id=actor_id,
        reason_code="operator_claimed",
        now=now,
    )
    escalation = _active_escalation(session, conversation)
    if escalation is not None:
        escalation.status = EscalationStatus.ASSIGNED
        escalation.assignee_actor_id = actor_id
        escalation.assigned_at = now
    return conversation


def release_conversation(
    session: Session,
    *,
    tenant_id: str,
    conversation_pk: int,
    actor_id: str,
    expected_version: int,
) -> Conversation:
    now = utc_now_naive()
    conversation = _finish_human_ownership(
        session,
        tenant_id=tenant_id,
        conversation_pk=conversation_pk,
        actor_id=actor_id,
        expected_version=expected_version,
        to_owner=ConversationOwner.AI,
        reason_code="operator_released",
        now=now,
    )
    conversation.ai_resumed_at = now
    return conversation


def resolve_conversation(
    session: Session,
    *,
    tenant_id: str,
    conversation_pk: int,
    actor_id: str,
    expected_version: int,
) -> Conversation:
    return _finish_human_ownership(
        session,
        tenant_id=tenant_id,
        conversation_pk=conversation_pk,
        actor_id=actor_id,
        expected_version=expected_version,
        to_owner=ConversationOwner.CLOSED,
        reason_code="operator_resolved",
        now=utc_now_naive(),
    )


def current_ownership(session: Session, context: ExecutionContext) -> tuple[ConversationOwner, int]:
    row = session.execute(
        select(Conversation.owner, Conversation.ownership_version).where(*_identity(context))
    ).one()
    return ConversationOwner(row.owner), row.ownership_version


def _finish_human_ownership(
    session: Session,
    *,
    tenant_id: str,
    conversation_pk: int,
    actor_id: str,
    expected_version: int,
    to_owner: ConversationOwner,
    reason_code: str,
    now: datetime,
) -> Conversation:
    conversation = _locked_for_transition(session, tenant_id, conversation_pk, expected_version)
    if conversation.owner != ConversationOwner.HUMAN or conversation.assignee_actor_id != actor_id:
        raise ConversationOwnershipChangedError()
    escalation = _active_escalation(session, conversation)
    if escalation is not None:
        escalation.status = EscalationStatus.RESOLVED
        escalation.resolved_at = now
    conversation.assignee_actor_id = None
    _transition(
        session,
        conversation,
        to_owner=to_owner,
        actor_id=actor_id,
        reason_code=reason_code,
        now=now,
    )
    return conversation


def _transition(
    session: Session,
    conversation: Conversation,
    *,
    to_owner: ConversationOwner,
    actor_id: str,
    reason_code: str,
    now: datetime,
) -> None:
    session.add(
        ConversationOwnershipEvent(
            tenant_id=conversation.tenant_id,
            conversation_pk=conversation.id,
            actor_id=actor_id,
            from_owner=conversation.owner,
            to_owner=to_owner,
            reason_code=reason_code,
            created_at=now,
        )
    )
    conversation.owner = to_owner
    conversation.ownership_version += 1
    conversation.updated_at = now
    session.flush()


def _identity(context: ExecutionContext) -> tuple[ColumnElement[bool], ...]:
    return (
        Conversation.tenant_id == context.tenant_id,
        Conversation.customer_id == context.effective_customer_id,
        Conversation.conversation_id == context.conversation_id,
    )


def _locked_conversation(session: Session, context: ExecutionContext) -> Conversation | None:
    return session.scalar(
        select(Conversation)
        .where(*_identity(context))
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _create_conversation(session: Session, context: ExecutionContext) -> Conversation:
    try:
        with session.begin_nested():
            conversation = Conversation(
                tenant_id=context.tenant_id,
                customer_id=context.effective_customer_id,
                conversation_id=context.conversation_id,
                owner=ConversationOwner.AI,
                ownership_version=0,
            )
            session.add(conversation)
        return conversation
    except IntegrityError:
        # A concurrent first turn created it; this request uses that row.
        existing = _locked_conversation(session, context)
        if existing is None:
            raise
        return existing


def _locked_for_transition(
    session: Session, tenant_id: str, conversation_pk: int, expected_version: int
) -> Conversation:
    conversation = session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_pk, Conversation.tenant_id == tenant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if conversation is None or conversation.ownership_version != expected_version:
        raise ConversationOwnershipChangedError()
    return conversation


def _active_escalation(session: Session, conversation: Conversation) -> Escalation | None:
    return session.scalar(
        select(Escalation).where(
            Escalation.conversation_pk == conversation.id,
            Escalation.tenant_id == conversation.tenant_id,
            Escalation.status.in_(_ACTIVE_ESCALATION_STATUSES),
        )
    )
