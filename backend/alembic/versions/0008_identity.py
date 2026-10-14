"""Verified email, administrator MFA and retriable mail delivery."""
from alembic import op
import sqlalchemy as sa
from app.db.models import Base
revision="0008_identity"
down_revision="0007_billing"
branch_labels=depends_on=None


def upgrade():
    for column in (sa.Column("email",sa.String(128),nullable=True),sa.Column("email_verified",sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column("totp_secret",sa.String(512),nullable=True),sa.Column("pending_totp_secret",sa.String(512),nullable=True),sa.Column("last_totp_step",sa.Integer(),nullable=False,server_default="-1"),sa.Column("recovery_codes",sa.JSON(),nullable=True)):
        op.add_column("users",column)
    op.create_unique_constraint("uq_users_email","users",["email"])
    for name in ("auth_challenges","mail_outbox"):
        Base.metadata.tables[name].create(op.get_bind(),checkfirst=True)


def downgrade():
    op.drop_table("mail_outbox");op.drop_table("auth_challenges")
    for column in ("email","email_verified","totp_secret","pending_totp_secret","last_totp_step","recovery_codes"):
        op.drop_column("users",column)
