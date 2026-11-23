"""agent studio: config versions, knowledge articles, quality and simulations

Revision ID: 20261122_0012
Revises: 20261122_0011
"""

from alembic import op
import sqlalchemy as sa


revision = "20261122_0012"
down_revision = "20261122_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_config_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("published_by", sa.String(length=200), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("tenant_id", "version", name="uq_agent_config_version"),
    )
    op.create_index(
        "ix_agent_config_versions_tenant_status",
        "agent_config_versions",
        ["tenant_id", "status"],
    )
    op.create_table(
        "knowledge_articles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("document_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "document_id", name="uq_knowledge_article_document"),
    )
    op.create_table(
        "quality_monitors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        sa.Column("phrases", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_quality_monitors_tenant", "quality_monitors", ["tenant_id"])
    op.create_table(
        "quality_flag_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("monitor_key", sa.String(length=80), nullable=False),
        sa.Column(
            "conversation_pk", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("reviewed_by", sa.String(length=200), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "monitor_key", "conversation_pk", name="uq_quality_flag_review"
        ),
    )
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=200), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("started_by", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=False),
        sa.Column("scenario_count", sa.Integer(), nullable=False),
        sa.Column("passed_count", sa.Integer(), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_simulation_runs_tenant_started", "simulation_runs", ["tenant_id", "started_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_simulation_runs_tenant_started", table_name="simulation_runs")
    op.drop_table("simulation_runs")
    op.drop_table("quality_flag_reviews")
    op.drop_index("ix_quality_monitors_tenant", table_name="quality_monitors")
    op.drop_table("quality_monitors")
    op.drop_table("knowledge_articles")
    op.drop_index("ix_agent_config_versions_tenant_status", table_name="agent_config_versions")
    op.drop_table("agent_config_versions")
