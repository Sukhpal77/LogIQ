"""Allow independent recovery notification records."""
from alembic import op
import sqlalchemy as sa

revision = "0009_recovery_notices"
down_revision = "0008_identity"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("notification_outbox", "incident_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.execute("DELETE FROM notification_outbox WHERE incident_id IS NULL")
    op.alter_column("notification_outbox", "incident_id", existing_type=sa.Integer(), nullable=False)
