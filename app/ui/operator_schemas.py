from datetime import datetime

from pydantic import BaseModel, Field


class OperatorIdentityView(BaseModel):
    actor_id: str
    display_name: str


class ConversationMessageView(BaseModel):
    sequence: int
    sender_type: str
    sender_name: str | None = None
    sent_by_me: bool = False
    content: str
    redacted: bool
    agent_run_id: str | None = None
    created_at: datetime


class EscalationSummaryView(BaseModel):
    priority: str
    reason: str
    summary: str
    status: str
    created_at: datetime | None = None


class OperatorConversationView(BaseModel):
    id: int
    conversation_id: str
    customer_id: int
    customer_name: str
    owner: str
    assigned_to_me: bool
    assignee_name: str | None = None
    ownership_version: int
    created_at: datetime
    updated_at: datetime
    last_message: ConversationMessageView | None = None
    escalation: EscalationSummaryView | None = None


class OperatorConversationDetailView(OperatorConversationView):
    messages: list[ConversationMessageView]


class OwnershipCommand(BaseModel):
    expected_version: int = Field(ge=0)


class OperatorReplyCommand(OwnershipCommand):
    content: str = Field(min_length=1, max_length=4000)
    client_message_id: str = Field(min_length=8, max_length=200)


class CustomerMessageView(BaseModel):
    """Customer-facing transcript entry; never carries operator actor identifiers."""

    sequence: int
    sender_type: str
    sender_name: str | None = None
    content: str
    created_at: datetime


class CustomerTranscriptView(BaseModel):
    conversation_id: str
    owner: str
    messages: list[CustomerMessageView]
