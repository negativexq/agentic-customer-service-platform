"""operator workspace: internal notes, saved replies, SLA targets, CSAT

Revision ID: 20261122_0013
Revises: 20261122_0012
"""

from alembic import op
import sqlalchemy as sa


revision = "20261122_0013"
down_revision = "20261122_0012"
branch_labels = None
depends_on = None


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False)


def upgrade() -> None:
    op.create_table(
        "conversation_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("author_actor_id", sa.String(length=200), nullable=False),
        sa.Column("author_name", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("redacted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversation_notes_conversation", "conversation_notes", ["conversation_pk", "id"])
    op.create_table(
        "saved_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("use_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_saved_replies_tenant", "saved_replies", ["tenant_id"])
    op.create_table(
        "sla_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("first_response_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "priority", name="uq_sla_targets_priority"),
    )
    op.create_table(
        "conversation_feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        _tenant(),
        sa.Column("conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_pk", name="uq_conversation_feedback"),
    )


def downgrade() -> None:
    op.drop_table("conversation_feedback")
    op.drop_table("sla_targets")
    op.drop_index("ix_saved_replies_tenant", table_name="saved_replies")
    op.drop_table("saved_replies")
    op.drop_index("ix_conversation_notes_conversation", table_name="conversation_notes")
    op.drop_table("conversation_notes")
