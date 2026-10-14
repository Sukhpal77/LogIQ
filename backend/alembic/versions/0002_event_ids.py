"""Deduplicate collector retries by stable event ID."""
from alembic import op
import sqlalchemy as sa

revision = "0002_event_ids"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("logs", "metrics"):
        op.add_column(table, sa.Column("event_id", sa.String(36), nullable=True))
        op.create_unique_constraint(f"uq_{table}_event_id", table, ["event_id"])


def downgrade():
    for table in ("logs", "metrics"):
        op.drop_constraint(f"uq_{table}_event_id", table, type_="unique")
        op.drop_column(table, "event_id")
