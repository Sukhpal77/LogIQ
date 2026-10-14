"""Verified provider events and customer invoices."""
from alembic import op
import sqlalchemy as sa
from app.db.models import Base
revision="0007_billing"
down_revision="0006_archiving"
branch_labels=depends_on=None


def upgrade():
    columns={c["name"] for c in sa.inspect(op.get_bind()).get_columns("subscriptions")}
    if "provider_id" not in columns:
        op.add_column("subscriptions",sa.Column("provider_id",sa.String(128),nullable=True))
        op.create_unique_constraint("uq_subscription_provider","subscriptions",["provider_id"])
    if "period_start" not in columns:
        op.add_column("subscriptions",sa.Column("period_start",sa.DateTime(),nullable=False,server_default=sa.func.now()))
    if "grace_until" not in columns:
        op.add_column("subscriptions",sa.Column("grace_until",sa.DateTime(),nullable=True))
    for name in ("payment_events","invoices"):
        Base.metadata.tables[name].create(op.get_bind(),checkfirst=True)


def downgrade():
    op.drop_table("invoices");op.drop_table("payment_events")
    for column in ("provider_id","period_start","grace_until"):
        op.drop_column("subscriptions",column)
