"""Operator workspace read/write models: customer context, notes, saved replies, SLA and CSAT."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

SlaState = Literal["ok", "at_risk", "breached"]
Priority = Literal["low", "medium", "high", "urgent"]


class SlaStatusView(BaseModel):
    priority: str
    target_minutes: int
    waiting_since: datetime
    due_at: datetime
    state: SlaState


class FeedbackView(BaseModel):
    rating: int
    comment: str
    resolved_by: str
    created_at: datetime


class CustomerProfileView(BaseModel):
    id: int
    name: str
    email: str
    customer_since: datetime


class CustomerOrderView(BaseModel):
    id: int
    status: str
    total_amount: Decimal
    created_at: datetime


class CustomerTicketView(BaseModel):
    id: int
    category: str
    status: str
    description: str
    order_id: int | None
    created_at: datetime


class CustomerConversationView(BaseModel):
    id: int
    conversation_id: str
    owner: str
    updated_at: datetime
    last_message: str | None
    rating: int | None


class CustomerContextView(BaseModel):
    customer: CustomerProfileView
    orders: list[CustomerOrderView]
    tickets: list[CustomerTicketView]
    other_conversations: list[CustomerConversationView]
    conversation_count: int
    average_rating: float | None
    feedback: FeedbackView | None


class NoteView(BaseModel):
    id: int
    author_name: str
    written_by_me: bool
    content: str
    redacted: bool
    created_at: datetime


class NoteCommand(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class SavedReplyView(BaseModel):
    id: int
    title: str
    content: str
    created_by: str
    updated_at: datetime
    use_count: int


class SavedReplyCommand(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=4000)


class SlaTargetView(BaseModel):
    priority: Priority
    first_response_minutes: int = Field(ge=1, le=10_080)


class SlaTargetsCommand(BaseModel):
    targets: list[SlaTargetView] = Field(min_length=1, max_length=4)


class FeedbackCommand(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)
