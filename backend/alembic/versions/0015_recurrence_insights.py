"""Customer recurrence evidence; additive, no notification changes."""
from alembic import op
import sqlalchemy as sa

revision = "0015_recurrence_insights"
down_revision = "0014_history_summaries"
branch_labels = depends_on = None


def upgrade():
    op.create_table("recurrence_insights",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "service", "fingerprint", name="uq_recurrence_pattern"))
    op.create_index("ix_recurrence_insights_tenant_id", "recurrence_insights", ["tenant_id"])
    op.create_index("ix_recurrence_tenant_updated", "recurrence_insights", ["tenant_id", "updated_at"])


def downgrade():
    op.drop_table("recurrence_insights")
