"""Retire application collection without deleting historical evidence."""
from alembic import op
import sqlalchemy as sa
revision="0006_archiving"
down_revision="0005_local_executor"
branch_labels=depends_on=None


def upgrade():
    for table,field,default in (("applications","archived",sa.false()),("services","active",sa.true())):
        if field not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}:
            op.add_column(table,sa.Column(field,sa.Boolean(),nullable=False,server_default=default))


def downgrade():
    op.drop_column("applications","archived")
    op.drop_column("services","active")
