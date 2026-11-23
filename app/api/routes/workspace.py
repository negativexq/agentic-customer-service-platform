"""Operator workspace: customer context, internal notes, saved replies, SLA targets and CSAT."""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes.operator import _conversation_or_404, _operator_name, _tenant
from app.auth.dependencies import (
    get_current_principal,
    require_studio_editor,
    require_support_operator,
    resolve_customer_scope,
)
from app.auth.models import Principal
from app.core.database import get_db
from app.core.time import utc_now_naive
from app.memory.dlp import redact_message_text
from app.models.entities import Conversation, Customer, Order, SupportTicket
from app.models.workspace import ConversationFeedback, ConversationNote, SavedReply
from app.services.conversation_messages import last_messages
from app.services.workspace import (
    add_note,
    feedback_for,
    list_notes,
    list_saved_replies,
    save_sla_targets,
    sla_targets,
    submit_feedback,
)
from app.ui.workspace_schemas import (
    CustomerContextView,
    CustomerConversationView,
    CustomerOrderView,
    CustomerProfileView,
    CustomerTicketView,
    FeedbackCommand,
    FeedbackView,
    NoteCommand,
    NoteView,
    Priority,
    SavedReplyCommand,
    SavedReplyView,
    SlaTargetsCommand,
    SlaTargetView,
)

router = APIRouter(tags=["operator-workspace"])
_CONTEXT_LIMIT = 10


def _feedback_view(row: ConversationFeedback | None) -> FeedbackView | None:
    if row is None:
        return None
    return FeedbackView(
        rating=row.rating,
        comment=row.comment,
        resolved_by=row.resolved_by,
        created_at=row.updated_at,
    )


@router.get("/operator/conversations/{conversation_pk}/context", response_model=CustomerContextView)
def customer_context(
    conversation_pk: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> CustomerContextView:
    """Everything an operator needs about the customer without leaving the conversation."""

    conversation = _conversation_or_404(session, principal, conversation_pk)
    tenant = _tenant(principal)
    customer = session.get(Customer, conversation.customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    orders = session.scalars(
        select(Order)
        .where(Order.customer_id == customer.id, Order.tenant_id == tenant)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .limit(_CONTEXT_LIMIT)
    )
    tickets = session.scalars(
        select(SupportTicket)
        .where(SupportTicket.customer_id == customer.id, SupportTicket.tenant_id == tenant)
        .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
        .limit(_CONTEXT_LIMIT)
    )
    conversation_filter = (
        Conversation.tenant_id == tenant,
        Conversation.customer_id == customer.id,
    )
    others = list(
        session.scalars(
            select(Conversation)
            .where(*conversation_filter, Conversation.id != conversation.id)
            .order_by(Conversation.updated_at.desc())
            .limit(_CONTEXT_LIMIT)
        )
    )
    total = session.scalar(
        select(func.count()).select_from(Conversation).where(*conversation_filter)
    )
    average = session.scalar(
        select(func.avg(ConversationFeedback.rating)).where(
            ConversationFeedback.tenant_id == tenant,
            ConversationFeedback.customer_id == customer.id,
        )
    )
    latest = last_messages(session, [item.id for item in others])
    ratings = feedback_for(session, [item.id for item in others] + [conversation.id])
    return CustomerContextView(
        customer=CustomerProfileView(
            id=customer.id,
            name=customer.name,
            email=customer.email,
            customer_since=customer.created_at,
        ),
        orders=[
            CustomerOrderView(
                id=o.id, status=o.status, total_amount=o.total_amount, created_at=o.created_at
            )
            for o in orders
        ],
        tickets=[
            CustomerTicketView(
                id=t.id,
                category=t.category,
                status=t.status,
                description=t.description,
                order_id=t.order_id,
                created_at=t.created_at,
            )
            for t in tickets
        ],
        other_conversations=[
            CustomerConversationView(
                id=item.id,
                conversation_id=item.conversation_id,
                owner=item.owner,
                updated_at=item.updated_at,
                last_message=latest[item.id].content if item.id in latest else None,
                rating=ratings[item.id].rating if item.id in ratings else None,
            )
            for item in others
        ],
        conversation_count=total or 0,
        average_rating=round(float(average), 2) if average is not None else None,
        feedback=_feedback_view(ratings.get(conversation.id)),
    )


def _note_view(note: ConversationNote, principal: Principal) -> NoteView:
    return NoteView(
        id=note.id,
        author_name=note.author_name,
        written_by_me=note.author_actor_id == principal.actor_id,
        content=note.content,
        redacted=note.redacted,
        created_at=note.created_at,
    )


@router.get("/operator/conversations/{conversation_pk}/notes", response_model=list[NoteView])
def notes(
    conversation_pk: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[NoteView]:
    _conversation_or_404(session, principal, conversation_pk)
    return [_note_view(note, principal) for note in list_notes(session, conversation_pk)]


@router.post(
    "/operator/conversations/{conversation_pk}/notes",
    response_model=NoteView,
    status_code=status.HTTP_201_CREATED,
)
def create_note(
    conversation_pk: int,
    command: NoteCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> NoteView:
    """Any operator who can read the conversation may leave a note; ownership is not required."""

    conversation = _conversation_or_404(session, principal, conversation_pk)
    note = add_note(
        session,
        conversation,
        actor_id=principal.actor_id,
        author_name=_operator_name(principal),
        content=command.content,
    )
    return _note_view(note, principal)


@router.delete(
    "/operator/conversations/{conversation_pk}/notes/{note_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_note(
    conversation_pk: int,
    note_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> Response:
    _conversation_or_404(session, principal, conversation_pk)
    note = session.get(ConversationNote, note_id)
    if note is None or note.conversation_pk != conversation_pk:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.author_actor_id != principal.actor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the author can delete a note."
        )
    session.delete(note)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _reply_view(row: SavedReply) -> SavedReplyView:
    return SavedReplyView(
        id=row.id,
        title=row.title,
        content=row.content,
        created_by=row.created_by,
        updated_at=row.updated_at,
        use_count=row.use_count,
    )


def _reply_or_404(session: Session, principal: Principal, reply_id: int) -> SavedReply:
    row = session.get(SavedReply, reply_id)
    if row is None or row.tenant_id != _tenant(principal):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved reply not found")
    return row


@router.get("/operator/saved-replies", response_model=list[SavedReplyView])
def saved_replies(
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[SavedReplyView]:
    return [_reply_view(row) for row in list_saved_replies(session, _tenant(principal))]


@router.post(
    "/operator/saved-replies", response_model=SavedReplyView, status_code=status.HTTP_201_CREATED
)
def create_saved_reply(
    command: SavedReplyCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> SavedReplyView:
    content, _ = redact_message_text(command.content)
    now = utc_now_naive()
    row = SavedReply(
        tenant_id=_tenant(principal),
        title=command.title.strip(),
        content=content,
        created_by=_operator_name(principal),
        created_at=now,
        updated_at=now,
        use_count=0,
    )
    session.add(row)
    session.commit()
    return _reply_view(row)


@router.put("/operator/saved-replies/{reply_id}", response_model=SavedReplyView)
def update_saved_reply(
    reply_id: int,
    command: SavedReplyCommand,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> SavedReplyView:
    row = _reply_or_404(session, principal, reply_id)
    row.title = command.title.strip()
    row.content, _ = redact_message_text(command.content)
    row.updated_at = utc_now_naive()
    session.commit()
    return _reply_view(row)


@router.delete("/operator/saved-replies/{reply_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_reply(
    reply_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> Response:
    session.delete(_reply_or_404(session, principal, reply_id))
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/operator/saved-replies/{reply_id}/used", response_model=SavedReplyView)
def saved_reply_used(
    reply_id: int,
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> SavedReplyView:
    row = _reply_or_404(session, principal, reply_id)
    row.use_count += 1
    session.commit()
    return _reply_view(row)


def _target_views(targets: dict[str, int]) -> list[SlaTargetView]:
    order: tuple[Priority, ...] = ("urgent", "high", "medium", "low")
    return [
        SlaTargetView(priority=priority, first_response_minutes=targets[priority])
        for priority in order
    ]


@router.get("/operator/sla-targets", response_model=list[SlaTargetView])
def get_sla_targets(
    principal: Principal = Depends(require_support_operator),
    session: Session = Depends(get_db),
) -> list[SlaTargetView]:
    return _target_views(sla_targets(session, _tenant(principal)))


@router.put("/operator/sla-targets", response_model=list[SlaTargetView])
def put_sla_targets(
    command: SlaTargetsCommand,
    principal: Principal = Depends(require_studio_editor),
    session: Session = Depends(get_db),
) -> list[SlaTargetView]:
    saved = save_sla_targets(
        session,
        _tenant(principal),
        principal.actor_id,
        {target.priority: target.first_response_minutes for target in command.targets},
    )
    return _target_views(saved)


@router.post("/conversations/{conversation_id}/feedback", response_model=FeedbackView)
def customer_feedback(
    conversation_id: str,
    command: FeedbackCommand,
    customer_id: int | None = Query(default=None, gt=0),
    principal: Principal = Depends(get_current_principal),
    session: Session = Depends(get_db),
) -> FeedbackView:
    """The customer rates their own conversation (CSAT, 1–5). Answering again replaces it."""

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
    row = submit_feedback(session, conversation, rating=command.rating, comment=command.comment)
    view = _feedback_view(row)
    assert view is not None
    return view
