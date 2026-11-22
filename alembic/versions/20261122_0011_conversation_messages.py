"""record conversation messages for operators

Revision ID: 20261122_0011
Revises: 20261103_0010
"""

from alembic import op
import sqlalchemy as sa


revision = "20261122_0011"
down_revision = "20261103_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        sa.Column("sender_actor_id", sa.String(length=200), nullable=True),
        sa.Column("sender_display_name", sa.String(length=200), nullable=True),
        sa.Column("agent_run_id", sa.String(length=200), nullable=True),
        sa.Column("client_message_id", sa.String(length=200), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("redaction_state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "conversation_pk", "sequence", name="uq_conversation_messages_sequence"
        ),
        sa.UniqueConstraint(
            "conversation_pk", "client_message_id", name="uq_conversation_messages_client_id"
        ),
    )
    op.create_index(
        "ix_conversation_messages_tenant_customer",
        "conversation_messages",
        ["tenant_id", "customer_id"],
    )
    op.create_index("ix_conversation_messages_expires", "conversation_messages", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_expires", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_tenant_customer", table_name="conversation_messages")
    op.drop_table("conversation_messages")
