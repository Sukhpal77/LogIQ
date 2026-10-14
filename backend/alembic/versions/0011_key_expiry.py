"""Expire newly issued application credentials."""
from alembic import op
import sqlalchemy as sa
revision="0011_key_expiry"
down_revision="0010_embedding_versions"
branch_labels=None
depends_on=None

def upgrade():
    connection=op.get_bind()
    if "expires_at" not in {c["name"] for c in sa.inspect(connection).get_columns("api_keys")}:
        op.add_column("api_keys",sa.Column("expires_at",sa.DateTime(),nullable=True))

def downgrade():
    op.drop_column("api_keys","expires_at")
