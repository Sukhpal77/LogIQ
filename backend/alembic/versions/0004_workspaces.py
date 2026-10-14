"""Versioned plans and isolated customer workspaces; retain legacy data."""
from alembic import op
import sqlalchemy as sa
from app.db.models import Base

revision = "0004_workspaces"
down_revision = "0003_notification_outbox"
branch_labels = depends_on = None

TABLES = ("logs", "metrics", "services", "dependencies", "incidents", "incident_memory", "notification_outbox")
NEW = ("organizations", "plans", "subscriptions", "applications", "api_keys", "workspace_settings", "usage_events", "audit_events")


def upgrade():
    for name in TABLES + ("users",):
        op.add_column(name, sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="1"))
        op.create_index(f"ix_{name}_tenant_id", name, ["tenant_id"])
    op.add_column("users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("owner", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"))
    for name in ("logs", "metrics"):
        op.drop_constraint(f"uq_{name}_event_id", name, type_="unique")
        op.create_unique_constraint(f"uq_{name}_tenant_event", name, ["tenant_id", "event_id"])
    op.drop_constraint("services_name_key", "services", type_="unique")
    op.create_unique_constraint("uq_service_tenant_name", "services", ["tenant_id", "name"])
    bind = op.get_bind()
    for name in NEW:
        Base.metadata.tables[name].create(bind)
    op.execute("INSERT INTO organizations (id, name, status, demo, created_at) VALUES (1, 'Legacy workspace', 'active', false, now())")
    op.execute("SELECT setval(pg_get_serial_sequence('organizations','id'), 1)")


def downgrade():
    # Multiple customers may have identical service names: export before rollback.
    for name in reversed(NEW):
        op.drop_table(name)
    op.drop_constraint("uq_service_tenant_name", "services", type_="unique")
    op.create_unique_constraint("services_name_key", "services", ["name"])
    op.drop_column("users", "active")
    op.drop_column("users", "owner")
    op.drop_column("users", "session_version")
    for name in ("logs", "metrics"):
        op.drop_constraint(f"uq_{name}_tenant_event", name, type_="unique")
        op.create_unique_constraint(f"uq_{name}_event_id", name, ["event_id"])
    for name in TABLES + ("users",):
        op.drop_index(f"ix_{name}_tenant_id", name)
        op.drop_column(name, "tenant_id")
