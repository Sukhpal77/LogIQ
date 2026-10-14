"""Structured incident resolutions and customer log digests."""
from alembic import op
import sqlalchemy as sa

revision = "0014_history_summaries"
down_revision = "0013_remove_automations"
branch_labels = depends_on = None


def upgrade():
    op.add_column("incidents", sa.Column("resolution", sa.JSON(), nullable=True))
    op.create_index("ix_incidents_tenant_service_created", "incidents", ["tenant_id", "service", "created_at"])
    op.create_index("ix_logs_tenant_timestamp", "logs", ["tenant_id", "timestamp"])
    op.create_table("log_summaries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("window_start", sa.DateTime(), nullable=False),
        sa.Column("window_end", sa.DateTime(), nullable=False),
        sa.Column("source_max_id", sa.BigInteger(), nullable=False),
        sa.Column("source_count", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "service", "window_start", "window_end", name="uq_log_summary_window"))
    op.create_index("ix_log_summaries_tenant_end", "log_summaries", ["tenant_id", "window_end"])
    op.create_index("ix_log_summaries_tenant_id", "log_summaries", ["tenant_id"])


def downgrade():
    op.drop_table("log_summaries")
    op.drop_index("ix_logs_tenant_timestamp", table_name="logs")
    op.drop_index("ix_incidents_tenant_service_created", table_name="incidents")
    op.drop_column("incidents", "resolution")
