"""add conversation ownership for human handoff

Revision ID: 20261103_0010
Revises: 20260824_0009
"""

from alembic import op
import sqlalchemy as sa


revision = "20261103_0010"
down_revision = "20260824_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("conversation_id", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=20), nullable=False),
        sa.Column("assignee_actor_id", sa.String(length=200), nullable=True),
        sa.Column("ownership_version", sa.Integer(), nullable=False),
        sa.Column("ai_resumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "customer_id", "conversation_id", name="uq_conversations_identity"
        ),
    )
    op.create_index(
        "ix_conversations_tenant_owner_updated",
        "conversations",
        ["tenant_id", "owner", "updated_at"],
    )
    op.create_table(
        "conversation_ownership_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("actor_id", sa.String(length=200), nullable=False),
        sa.Column("from_owner", sa.String(length=20), nullable=False),
        sa.Column("to_owner", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_conversation_ownership_events_conversation",
        "conversation_ownership_events",
        ["conversation_pk", "id"],
    )
    op.add_column(
        "escalations",
        sa.Column(
            "conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True
        ),
    )
    op.add_column(
        "escalations", sa.Column("assignee_actor_id", sa.String(length=200), nullable=True)
    )
    op.add_column("escalations", sa.Column("assigned_at", sa.DateTime(), nullable=True))
    op.add_column("escalations", sa.Column("resolved_at", sa.DateTime(), nullable=True))
    op.create_index(
        "uq_escalations_active_conversation",
        "escalations",
        ["conversation_pk"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'assigned')"),
        sqlite_where=sa.text("status IN ('queued', 'assigned')"),
    )


def downgrade() -> None:
    op.drop_index("uq_escalations_active_conversation", table_name="escalations")
    op.drop_column("escalations", "resolved_at")
    op.drop_column("escalations", "assigned_at")
    op.drop_column("escalations", "assignee_actor_id")
    op.drop_column("escalations", "conversation_pk")
    op.drop_index(
        "ix_conversation_ownership_events_conversation",
        table_name="conversation_ownership_events",
    )
    op.drop_table("conversation_ownership_events")
    op.drop_index("ix_conversations_tenant_owner_updated", table_name="conversations")
    op.drop_table("conversations")
