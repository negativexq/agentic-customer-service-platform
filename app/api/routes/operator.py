"""Operator inbox: read every recorded conversation, take one over, reply, and hand it back."""

from collections.abc import Callable, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    get_current_principal,
    require_support_operator,
    resolve_customer_scope,
)
from app.auth.models import Principal
from app.auth.scope import operator_customer_filter, operator_may_access
from app.core.config import get_settings
from app.core.database import get_db
from app.core.time import utc_now_naive
from app.models.entities import (
    Conversation,
    ConversationMessage,
    ConversationOwner,
    Customer,
    Escalation,
    EscalationStatus,
    MessageSender,
)
from app.services.conversation_messages import (
    append_message,
    delete_customer_messages,
    last_messages,
    list_messages,
)
from app.services.conversations import (
    ConversationOwnershipChangedError,
    claim_conversation,
    release_conversation,
    resolve_conversation,
)
from app.services.workspace import feedback_for, sla_statuses, sla_targets
from app.ui.operator_schemas import (
    ConversationMessageView,
    CustomerMessageView,
    CustomerTranscriptView,
    EscalationSummaryView,
    OperatorConversationDetailView,
    OperatorConversationView,
    OperatorIdentityView,
    OperatorReplyCommand,
    OperatorSettingsView,
    OwnershipCommand,
)

router = APIRouter(tags=["operator"])

_CONFLICT = "Conversation ownership changed. Reload and try again."


def _tenant(principal: Principal) -> str:
    return principal.tenant_id or "default"


def _operator_name(principal: Principal) -> str:
    return principal.display_name or "Operator"


def _message_view(message: ConversationMessage, principal: Principal) -> ConversationMessageView:
    return ConversationMessageView(
        sequence=message.sequence,
        sender_type=message.sender_type,
        sender_name=message.sender_display_name,
        sent_by_me=message.sender_actor_id is not None
        and message.sender_actor_id == principal.actor_id,
        content=message.content,
        redacted=message.redaction_state == "redacted",
        agent_run_id=message.agent_run_id,
        created_at=message.created_at,
    )


def _active_escalations(session: Session, pks: Sequence[int]) -> dict[int, Escalation]:
    if not pks:
        return {}
    rows = session.scalars(
        select(Escalation).where(Escalation.conversation_pk.in_(pks)).order_by(Escalation.id)
    )
    # Prefer an open escalation; otherwise keep the latest for context.
    result: dict[int, Escalation] = {}
    for row in rows:
        assert row.conversation_pk is not None
        current = result.get(row.conversation_pk)
        open_now = row.status in (EscalationStatus.QUEUED, EscalationStatus.ASSIGNED)
        if current is None or open_now or current.status == EscalationStatus.RESOLVED:
            result[row.conversation_pk] = row
    return result


def _assignee_names(session: Session, pks: Sequence[int]) -> dict[int, str]:
    """Latest operator display name per conversation, from the recorded transcript."""

    if not pks:
        return {}
    rows = session.execute(
        select(ConversationMessage.conversation_pk, ConversationMessage.sender_display_name)
        .where(
            ConversationMessage.conversation_pk.in_(pks),
            ConversationMessage.sender_type == MessageSender.OPERATOR,
            ConversationMessage.sender_display_name.is_not(None),
        )
        .order_by(ConversationMessage.sequence)
    )
    return {pk: name for pk, name in rows if name}


def _summaries(
    session: Session, principal: Principal, conversations: Sequence[Conversation]
) -> list[OperatorConversationView]:
    pks = [conversation.id for conversation in conversations]
    customers = {
        row.id: row.name
        for row in session.scalars(
            select(Customer).where(Customer.id.in_({c.customer_id for c in conversations}))
        )
    }
    latest = last_messages(session, pks)
    escalations = _active_escalations(session, pks)
    assignee_names = _assignee_names(session, pks)
    slas = sla_statuses(
        session, conversations, escalations, sla_targets(session, _tenant(principal))
    )
    ratings = feedback_for(session, pks)
    views: list[OperatorConversationView] = []
    for conversation in conversations:
        mine = conversation.assignee_actor_id == principal.actor_id
        escalation = escalations.get(conversation.id)
        assignee_name = None
        if conversation.assignee_actor_id is not None:
            assignee_name = (
                _operator_name(principal) if mine else assignee_names.get(conversation.id)
            )
        message = latest.get(conversation.id)
        views.append(
            OperatorConversationView(
                id=conversation.id,
                conversation_id=conversation.conversation_id,
                customer_id=conversation.customer_id,
                customer_name=customers.get(
                    conversation.customer_id, f"Customer #{conversation.customer_id}"
                ),
                owner=conversation.owner,
                assigned_to_me=mine,
                assignee_name=assignee_name,
                ownership_version=conversation.ownership_version,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                last_message=_message_view(message, principal) if message else None,
                escalation=EscalationSummaryView(
                    priority=escalation.priority,
                    reason=escalation.reason,
                    summary=escalation.summary,
                    status=escalation.status,
                    created_at=escalation.created_at,
                )
                if escalation
                else None,
                sla=slas.get(conversation.id),
                csat_rating=ratings[conversation.id].rating if conversation.id in ratings else None,
            )
        )
    return views


def _conversation_or_404(
    session: Session, principal: Principal, conversation_pk: int
) -> Conversation:
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.id == conversation_pk, Conversation.tenant_id == _tenant(principal)
        )
    )
    if conversation is None or not operator_may_access(principal, conversation.customer_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def _detail(
    session: Session, principal: Principal, conversation: Conversation, after: int
) -> OperatorConversationDetailView:
    summary = _summaries(session, principal, [conversation])[0]
    messages = [
        _message_view(m, principal)
        for m in list_messages(session, conversation, after_sequence=after)
    ]
    return OperatorConversationDetailView(**summary.model_dump(), messages=messages)


@router.get("/operator/me", response_model=OperatorIdentityView)
def operator_identity(
    principal: Principal = Depends(require_support_operator),
) -> OperatorIdentityView:
    return OperatorIdentityView(actor_id=principal.actor_id, display_name=_operator_name(principal))


@router.get("/operator/settings", response_model=OperatorSettingsView)
def operator_settings(
    principal: Principal = Depends(require_support_operator),
) -> OperatorSettingsView:
    settings = get_settings()
    scope = operator_customer_filter(principal)
    return OperatorSettingsView(
        actor_id=principal.actor_id,
        display_name=_operator_name(principal),
        sign_in_method=principal.principal_type.value,
        tenant_id=principal.tenant_id or "default",
        roles=list(principal.roles),
        customer_scope="tenant" if scope is None else "assigned_customers",
        scoped_customer_count=None if scope is None else len(scope),
        message_retention_days=settings.conversation_message_ttl_days,
        memory_retention_days=settings.memory_default_ttl_days,
        support_context_retention_days=settings.memory_support_context_ttl_days,
    )


@router.get("/operator/conversations", response_model=list[OperatorConversationView])
def operator_conversations(
    owner: ConversationOwner | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[OperatorConversationView]:
    statement = (
        select(Conversation)
        .where(Conversation.tenant_id == _tenant(principal))
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
    )
    if owner is not None:
        statement = statement.where(Conversation.owner == owner)
    allowed = operator_customer_filter(principal)
    if allowed is not None:
        statement = statement.where(Conversation.customer_id.in_(allowed))
    return _summaries(session, principal, list(session.scalars(statement)))


@router.get(
    "/operator/conversations/{conversation_pk}", response_model=OperatorConversationDetailView
)
def operator_conversation(
    conversation_pk: int,
    after: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> OperatorConversationDetailView:
    conversation = _conversation_or_404(session, principal, conversation_pk)
    return _detail(session, principal, conversation, after)


def _transition(
    session: Session,
    principal: Principal,
    conversation_pk: int,
    command: OwnershipCommand,
    operation: Callable[..., Conversation],
    notice: str,
) -> OperatorConversationDetailView:
    _conversation_or_404(session, principal, conversation_pk)
    try:
        conversation = operation(
            session,
            tenant_id=_tenant(principal),
            conversation_pk=conversation_pk,
            actor_id=principal.actor_id,
            expected_version=command.expected_version,
        )
        append_message(session, conversation, sender=MessageSender.SYSTEM, content=notice)
        session.commit()
    except ConversationOwnershipChangedError:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_CONFLICT) from None
    return _detail(session, principal, conversation, 0)


@router.post(
    "/operator/conversations/{conversation_pk}/claim", response_model=OperatorConversationDetailView
)
def claim(
    conversation_pk: int,
    command: OwnershipCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> OperatorConversationDetailView:
    notice = f"{_operator_name(principal)} joined the conversation."
    return _transition(session, principal, conversation_pk, command, claim_conversation, notice)


@router.post(
    "/operator/conversations/{conversation_pk}/release",
    response_model=OperatorConversationDetailView,
)
def release(
    conversation_pk: int,
    command: OwnershipCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> OperatorConversationDetailView:
    notice = f"{_operator_name(principal)} handed the conversation back to the AI assistant."
    return _transition(session, principal, conversation_pk, command, release_conversation, notice)


@router.post(
    "/operator/conversations/{conversation_pk}/resolve",
    response_model=OperatorConversationDetailView,
)
def resolve(
    conversation_pk: int,
    command: OwnershipCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> OperatorConversationDetailView:
    notice = f"{_operator_name(principal)} resolved the conversation."
    return _transition(session, principal, conversation_pk, command, resolve_conversation, notice)


@router.post(
    "/operator/conversations/{conversation_pk}/messages",
    response_model=OperatorConversationDetailView,
)
def reply(
    conversation_pk: int,
    command: OperatorReplyCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> OperatorConversationDetailView:
    _conversation_or_404(session, principal, conversation_pk)
    conversation = session.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_pk, Conversation.tenant_id == _tenant(principal))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert conversation is not None
    if (
        conversation.owner != ConversationOwner.HUMAN
        or conversation.assignee_actor_id != principal.actor_id
        or conversation.ownership_version != command.expected_version
    ):
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_CONFLICT)
    append_message(
        session,
        conversation,
        sender=MessageSender.OPERATOR,
        content=command.content,
        sender_actor_id=principal.actor_id,
        sender_display_name=_operator_name(principal),
        client_message_id=command.client_message_id,
    )
    conversation.updated_at = utc_now_naive()
    session.commit()
    return _detail(session, principal, conversation, 0)


@router.delete("/operator/customers/{customer_id}/messages")
def delete_messages(
    customer_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> dict[str, int]:
    if not operator_may_access(principal, customer_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    deleted = delete_customer_messages(
        session, tenant_id=_tenant(principal), customer_id=customer_id
    )
    return {"deleted": deleted}


@router.get("/conversations/{conversation_id}/messages", response_model=CustomerTranscriptView)
def customer_transcript(
    conversation_id: str,
    customer_id: int | None = Query(default=None, gt=0),
    after: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
) -> CustomerTranscriptView:
    """Bounded polling for the customer's own conversation, including operator replies."""

    scope = resolve_customer_scope(principal, customer_id)
    conversation = session.scalar(
        select(Conversation).where(
            Conversation.tenant_id == _tenant(scope.principal),
            Conversation.customer_id == scope.customer_id,
            Conversation.conversation_id == conversation_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return CustomerTranscriptView(
        conversation_id=conversation_id,
        owner=conversation.owner,
        messages=[
            CustomerMessageView(
                sequence=message.sequence,
                sender_type=message.sender_type,
                sender_name=message.sender_display_name,
                content=message.content,
                created_at=message.created_at,
            )
            for message in list_messages(session, conversation, after_sequence=after)
        ],
    )
