from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.time import utc_now_naive


class OrderStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RefundStatus(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    PROCESSING = "processing"
    COMPLETED = "completed"


class EscalationPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class EscalationStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    RESOLVED = "resolved"


class ConversationOwner(StrEnum):
    AI = "ai"
    QUEUED = "queued"
    HUMAN = "human"
    CLOSED = "closed"


class MessageSender(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    OPERATOR = "operator"
    SYSTEM = "system"


class MessageRedaction(StrEnum):
    NOT_REQUIRED = "not_required"
    REDACTED = "redacted"


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TenantStatus] = mapped_column(
        String(20), nullable=False, default=TenantStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_customer_tenant_email"),
        Index("ix_customers_tenant_id", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="customer")
    refund_requests: Mapped[list["RefundRequest"]] = relationship(back_populates="customer")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="customer")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_tenant_customer", "tenant_id", "customer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)
    status: Mapped[OrderStatus] = mapped_column(String(20), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="orders")
    tickets: Mapped[list["SupportTicket"]] = relationship(back_populates="order")
    refund_requests: Mapped[list["RefundRequest"]] = relationship(back_populates="order")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="order")


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (Index("ix_tickets_tenant_customer", "tenant_id", "customer_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="tickets")
    order: Mapped[Order | None] = relationship(back_populates="tickets")


class RefundRequest(Base):
    __tablename__ = "refund_requests"
    __table_args__ = (
        Index("ix_refunds_tenant_customer", "tenant_id", "customer_id"),
        Index(
            "uq_refund_requests_active_order",
            "tenant_id",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'approved', 'processing')"),
            sqlite_where=text("status IN ('requested', 'approved', 'processing')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RefundStatus] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="refund_requests")
    order: Mapped[Order] = relationship(back_populates="refund_requests")


class Conversation(Base):
    """Source of truth for who may respond; checkpoints are scoped per actor."""

    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "customer_id", "conversation_id", name="uq_conversations_identity"
        ),
        Index("ix_conversations_tenant_owner_updated", "tenant_id", "owner", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[ConversationOwner] = mapped_column(String(20), nullable=False)
    assignee_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ownership_version: Mapped[int] = mapped_column(nullable=False, default=0)
    ai_resumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)


class ConversationOwnershipEvent(Base):
    """Append-only transition history; it never contains message text."""

    __tablename__ = "conversation_ownership_events"
    __table_args__ = (
        Index("ix_conversation_ownership_events_conversation", "conversation_pk", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    conversation_pk: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    from_owner: Mapped[ConversationOwner] = mapped_column(String(20), nullable=False)
    to_owner: Mapped[ConversationOwner] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        Index("ix_escalations_tenant_customer", "tenant_id", "customer_id"),
        Index(
            "uq_escalations_active_conversation",
            "conversation_pk",
            unique=True,
            postgresql_where=text("status IN ('queued', 'assigned')"),
            sqlite_where=text("status IN ('queued', 'assigned')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True, nullable=False)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("support_tickets.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[EscalationPriority] = mapped_column(String(20), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[EscalationStatus] = mapped_column(String(20), nullable=False)
    conversation_pk: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id"), nullable=True
    )
    assignee_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    customer: Mapped[Customer] = relationship(back_populates="escalations")
    ticket: Mapped[SupportTicket | None] = relationship()
    order: Mapped[Order | None] = relationship(back_populates="escalations")


class BusinessActionReceipt(Base):
    __tablename__ = "business_action_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "actor_id",
            "operation",
            "idempotency_key",
            name="uq_business_action_receipt_scope",
        ),
        Index("ix_business_receipts_tenant_customer", "tenant_id", "customer_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    customer_id: Mapped[int] = mapped_column(index=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_id: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)


class PolicyAuditRecord(Base):
    """Durable operational policy evidence; never an authorization source."""

    __tablename__ = "policy_audit_events"
    __table_args__ = (
        Index("ix_policy_audit_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_policy_audit_conversation_created", "conversation_id", "created_at", "id"),
        Index("ix_policy_audit_customer_created", "effective_customer_id", "created_at", "id"),
        Index("ix_policy_audit_request_created", "request_id", "created_at", "id"),
        Index("ix_policy_audit_action_created", "action_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    event_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    effective_customer_id: Mapped[int] = mapped_column(index=True, nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[int] = mapped_column(nullable=False)
    policy_outcome: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    stage: Mapped[str] = mapped_column(String(60), nullable=False)
    confirmation_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revalidation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)


class AgentRunProjectionRecord(Base):
    """Durable operator read model; never an authorization or execution source."""

    __tablename__ = "agent_run_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_id", name="uq_agent_run_projection_tenant_run"),
        Index("ix_agent_run_projection_tenant_created", "tenant_id", "created_at", "id"),
        Index(
            "ix_agent_run_projection_conversation_created",
            "conversation_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_agent_run_projection_customer_created",
            "effective_customer_id",
            "created_at",
            "id",
        ),
        Index("ix_agent_run_projection_created", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    run_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_id: Mapped[str] = mapped_column(String(200), nullable=False)
    conversation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effective_customer_id: Mapped[int] = mapped_column(nullable=False)
    actor_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    intent: Mapped[str] = mapped_column(String(80), nullable=False)
    request_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    duration_ms: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    path: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    failure_category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    degraded_components: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    recovery_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    memory_item_count: Mapped[int] = mapped_column(nullable=False)
    memory_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    memory_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    tools: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    policy: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    rag_documents: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    retrieval_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    trace: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False)


class ConversationMessage(Base):
    """Recorded transcript text after DLP redaction; display data, never an execution input."""

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("conversation_pk", "sequence", name="uq_conversation_messages_sequence"),
        UniqueConstraint(
            "conversation_pk", "client_message_id", name="uq_conversation_messages_client_id"
        ),
        Index("ix_conversation_messages_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_conversation_messages_expires", "expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), ForeignKey("tenants.id"), nullable=False)
    conversation_pk: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    customer_id: Mapped[int] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False)
    sender_type: Mapped[MessageSender] = mapped_column(String(20), nullable=False)
    sender_actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    client_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    redaction_state: Mapped[MessageRedaction] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now_naive, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)


class QualityMonitor(Base):
    """Operator-defined phrase monitor over recorded conversations (Watchtower-style QA)."""

    __tablename__ = "quality_monitors"
    __table_args__ = (Index("ix_quality_monitors_tenant", "tenant_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sender_type: Mapped[str] = mapped_column(String(20), nullable=False)
    phrases: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)


class QualityFlagReview(Base):
    """An operator's verdict on one flagged conversation for one monitor."""

    __tablename__ = "quality_flag_reviews"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "monitor_key", "conversation_pk", name="uq_quality_flag_review"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    monitor_key: Mapped[str] = mapped_column(String(80), nullable=False)
    conversation_pk: Mapped[int] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    reviewed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)


class SimulationRun(Base):
    """A batch of scripted, isolated agent simulations and their pass/fail results."""

    __tablename__ = "simulation_runs"
    __table_args__ = (Index("ix_simulation_runs_tenant_started", "tenant_id", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    started_by: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    finished_at: Mapped[datetime] = mapped_column(nullable=False)
    scenario_count: Mapped[int] = mapped_column(nullable=False)
    passed_count: Mapped[int] = mapped_column(nullable=False)
    results: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)


class AgentConfigVersion(Base):
    """One version of operator-managed agent configuration (guidance and action modes).

    Exactly one version per tenant is published and read by the runtime; at most one is a
    draft. Configuration can narrow what the agent may do but never grants new authority.
    """

    __tablename__ = "agent_config_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_agent_config_version"),
        Index("ix_agent_config_versions_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    version: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
    published_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)


class KnowledgeArticle(Base):
    """Console-managed knowledge article; overrides or extends the built-in markdown corpus."""

    __tablename__ = "knowledge_articles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_id", name="uq_knowledge_article_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("tenants.id"), nullable=False, default="default"
    )
    document_id: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(nullable=False, default=utc_now_naive)
