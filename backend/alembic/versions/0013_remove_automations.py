"""Retire restart automation without changing logs, metrics or subscriptions."""
from alembic import op
import sqlalchemy as sa

revision = "0013_remove_automations"
down_revision = "0012_usage_bigint"
branch_labels = depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("recovery_commands"):
        op.drop_table("recovery_commands")
    op.execute("DELETE FROM api_keys WHERE scope = 'executor'")
    op.execute("DELETE FROM workspace_settings WHERE kind IN ('automation', 'recovery')")
    # Remove the retired entitlement while preserving purchased prices/other limits.
    op.execute("UPDATE plans SET limits = (limits::jsonb - 'auto_recovery')::json WHERE limits::jsonb ? 'auto_recovery'")


def downgrade():
    raise RuntimeError("Automation data was removed intentionally. Restore the pre-removal backup to recover the retired feature data.")
