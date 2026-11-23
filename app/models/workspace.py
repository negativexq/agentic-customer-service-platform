"""Operator workspace records: internal notes, saved replies, service levels and CSAT."""

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utc_now_naive


class ConversationNote(Base):
    """Operator-only note on a conversation. Never part of the customer transcript or the agent."""

    __tablename__ = "conversation_notes"
    __table_args__ = (Index("ix_conversation_notes_conversation", "conversation_pk", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    conversation_pk: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    author_actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    author_name: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)


class SavedReply(Base):
    """A reusable operator reply (macro). Inserted into the composer, never sent automatically."""

    __tablename__ = "saved_replies"
    __table_args__ = (Index("ix_saved_replies_tenant", "tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    use_count: Mapped[int] = mapped_column(nullable=False, default=0)


class SlaTarget(Base):
    """First-response target for customers waiting on a person, per escalation priority."""

    __tablename__ = "sla_targets"
    __table_args__ = (UniqueConstraint("tenant_id", "priority", name="uq_sla_targets_priority"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    first_response_minutes: Mapped[int] = mapped_column(nullable=False)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)


class ConversationFeedback(Base):
    """The customer's satisfaction rating for a conversation; the latest answer wins."""

    __tablename__ = "conversation_feedback"
    __table_args__ = (UniqueConstraint("conversation_pk", name="uq_conversation_feedback"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    conversation_pk: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(nullable=False)
    rating: Mapped[int] = mapped_column(nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    resolved_by: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
