"""Durable notification retries."""
from alembic import op
import sqlalchemy as sa

revision = "0003_notification_outbox"
down_revision = "0002_event_ids"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("notification_outbox",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("incident_id", sa.Integer, sa.ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_attempt", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime, nullable=True))


def downgrade():
    op.drop_table("notification_outbox")
