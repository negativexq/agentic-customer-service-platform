from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.entities import (
    EscalationPriority,
    EscalationStatus,
    OrderStatus,
    RefundStatus,
    TicketStatus,
)


class CustomerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    created_at: datetime


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime
    refund_status: str | None = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    order_id: int | None
    category: str
    status: TicketStatus
    description: str
    created_at: datetime


class RefundRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    order_id: int
    reason: str
    status: RefundStatus
    created_at: datetime


class EscalationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    ticket_id: int | None
    order_id: int | None
    reason: str
    priority: EscalationPriority
    summary: str
    status: EscalationStatus
    created_at: datetime
