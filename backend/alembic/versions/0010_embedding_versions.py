"""Keep fallback vectors separate from semantic model vectors."""
from alembic import op
import sqlalchemy as sa
revision="0010_embedding_versions"
down_revision="0009_recovery_notices"
branch_labels=None
depends_on=None

def upgrade():
    op.add_column("incident_memory", sa.Column("embedding_version",sa.String(160),nullable=False,server_default="legacy-unknown"))

def downgrade():
    op.drop_column("incident_memory","embedding_version")
