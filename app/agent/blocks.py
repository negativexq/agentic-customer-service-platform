"""Structured reply blocks: tables, cards and quick replies drawn next to the reply text.

Blocks are built by the server from customer-scoped business records, never by the model, so
the numbers in a table are the numbers in the database. The reply text stays the source a
customer can read anywhere (email, translation); blocks only present the same facts richer.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order, RefundRequest, SupportTicket

_REFERENCE = re.compile(r"#([1-9][0-9]*)")
_MAX_ROWS = 10


class OrderRow(BaseModel):
    id: int
    status: str
    total_amount: Decimal
    created_at: datetime
    refund_status: str | None = None


class TicketRow(BaseModel):
    id: int
    status: str
    category: str
    order_id: int | None = None
    created_at: datetime
    description: str | None = None


class QuickReply(BaseModel):
    label: str
    reply: str
    tone: Literal["primary", "neutral"] = "neutral"


class OrdersBlock(BaseModel):
    type: Literal["orders"] = "orders"
    orders: list[OrderRow]


class OrderBlock(BaseModel):
    type: Literal["order"] = "order"
    order: OrderRow


class TicketsBlock(BaseModel):
    type: Literal["tickets"] = "tickets"
    tickets: list[TicketRow]


class TicketBlock(BaseModel):
    type: Literal["ticket"] = "ticket"
    ticket: TicketRow


class QuickRepliesBlock(BaseModel):
    type: Literal["quick_replies"] = "quick_replies"
    purpose: Literal["confirmation", "handoff"]
    replies: list[QuickReply]


ResponseBlock = Annotated[
    OrdersBlock | OrderBlock | TicketsBlock | TicketBlock | QuickRepliesBlock,
    Field(discriminator="type"),
]

_CONFIRMATION_REPLIES = {
    "en": [
        QuickReply(label="Yes, proceed", reply="Yes, proceed", tone="primary"),
        QuickReply(label="No", reply="No"),
    ],
    "tr": [
        QuickReply(label="Evet, onaylıyorum", reply="Evet, onaylıyorum", tone="primary"),
        QuickReply(label="Hayır", reply="Hayır"),
    ],
}
_HANDOFF_REPLIES = {
    "en": [QuickReply(label="Connect me to a person", reply="Connect me to a person now.")],
    "tr": [QuickReply(label="Beni bir temsilciye bağla", reply="Beni şimdi bir temsilciye bağla.")],
}


def build_blocks(
    session: Session,
    *,
    tenant_id: str,
    customer_id: int,
    tool_name: str | None,
    tool_executed: bool,
    message: str,
    language: str | None = None,
    awaiting_confirmation: bool = False,
    handoff_offered: bool = False,
) -> list[ResponseBlock]:
    """Blocks for one assistant reply; empty when there is nothing to show beyond the text."""

    blocks: list[ResponseBlock] = []
    if tool_executed and tool_name:
        blocks.extend(_data_blocks(session, tenant_id, customer_id, tool_name, message))
    lang = "tr" if language in {"tr", "Turkish"} else "en"
    if awaiting_confirmation:
        blocks.append(
            QuickRepliesBlock(purpose="confirmation", replies=list(_CONFIRMATION_REPLIES[lang]))
        )
    elif handoff_offered:
        blocks.append(QuickRepliesBlock(purpose="handoff", replies=list(_HANDOFF_REPLIES[lang])))
    return blocks


def _data_blocks(
    session: Session, tenant_id: str, customer_id: int, tool_name: str, message: str
) -> list[ResponseBlock]:
    if tool_name == "get_customer_orders":
        orders = _orders(session, tenant_id, customer_id)
        return [OrdersBlock(orders=orders)] if orders else []
    if tool_name == "get_customer_tickets":
        tickets = _tickets(session, tenant_id, customer_id)
        return [TicketsBlock(tickets=tickets)] if tickets else []
    reference = _first_reference(message)
    if reference is None:
        return []
    if tool_name in {"get_order", "cancel_order", "request_refund"}:
        order = _orders(session, tenant_id, customer_id, order_id=reference)
        return [OrderBlock(order=order[0])] if order else []
    if tool_name in {"get_ticket", "create_support_ticket"}:
        ticket = _tickets(session, tenant_id, customer_id, ticket_id=reference, detail=True)
        return [TicketBlock(ticket=ticket[0])] if ticket else []
    return []


def _first_reference(message: str) -> int | None:
    match = _REFERENCE.search(message)
    return int(match.group(1)) if match else None


def _orders(
    session: Session, tenant_id: str, customer_id: int, *, order_id: int | None = None
) -> list[OrderRow]:
    query = select(Order).where(Order.tenant_id == tenant_id, Order.customer_id == customer_id)
    if order_id is not None:
        query = query.where(Order.id == order_id)
    orders = list(session.scalars(query.order_by(Order.created_at, Order.id).limit(_MAX_ROWS)))
    refunds: dict[int, str] = {}
    if orders:
        for refund in session.scalars(
            select(RefundRequest)
            .where(
                RefundRequest.tenant_id == tenant_id,
                RefundRequest.order_id.in_([order.id for order in orders]),
            )
            .order_by(RefundRequest.created_at, RefundRequest.id)
        ):
            refunds[refund.order_id] = str(refund.status)
    return [
        OrderRow(
            id=order.id,
            status=str(order.status),
            total_amount=order.total_amount,
            created_at=order.created_at,
            refund_status=refunds.get(order.id),
        )
        for order in orders
    ]


def _tickets(
    session: Session,
    tenant_id: str,
    customer_id: int,
    *,
    ticket_id: int | None = None,
    detail: bool = False,
) -> list[TicketRow]:
    query = select(SupportTicket).where(
        SupportTicket.tenant_id == tenant_id, SupportTicket.customer_id == customer_id
    )
    if ticket_id is not None:
        query = query.where(SupportTicket.id == ticket_id)
    tickets = session.scalars(
        query.order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc()).limit(_MAX_ROWS)
    )
    return [
        TicketRow(
            id=ticket.id,
            status=str(ticket.status),
            category=ticket.category,
            order_id=ticket.order_id,
            created_at=ticket.created_at,
            description=ticket.description[:280] if detail else None,
        )
        for ticket in tickets
    ]
