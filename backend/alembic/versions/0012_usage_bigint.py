"""Allow byte totals larger than a signed 32-bit integer."""
from alembic import op
import sqlalchemy as sa
revision="0012_usage_bigint"
down_revision="0011_key_expiry"
branch_labels=None
depends_on=None

def upgrade():
    op.alter_column("usage_events","amount",existing_type=sa.Integer(),type_=sa.BigInteger())

def downgrade():
    # Refuse narrowing when values cannot fit rather than losing accounting data.
    op.alter_column("usage_events","amount",existing_type=sa.BigInteger(),type_=sa.Integer())
