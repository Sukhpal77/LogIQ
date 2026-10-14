"""Separate local executor credentials and durable command history."""
from alembic import op
import sqlalchemy as sa
from app.db.models import Base
revision="0005_local_executor"
down_revision="0004_workspaces"
branch_labels=depends_on=None


def upgrade():
    existing={c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_keys")}
    if "scope" not in existing:
        op.add_column("api_keys",sa.Column("scope",sa.String(24),nullable=False,server_default="ingest"))
    # Historical migrations must also run with the current metadata, where this
    # retired feature is absent. Existing databases keep their recorded revision.
    table = Base.metadata.tables.get("recovery_commands")
    if table is not None:
        table.create(op.get_bind(),checkfirst=True)


def downgrade():
    if sa.inspect(op.get_bind()).has_table("recovery_commands"):
        op.drop_table("recovery_commands")
    op.drop_column("api_keys","scope")
