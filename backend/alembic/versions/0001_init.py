"""Initial schema — users, logs, metrics, services, dependencies, incidents, incident_memory.

Revision ID: 0001_init
Revises:
Create Date: 2026-04-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001_init"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(64), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="devops"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("attrs", sa.JSON, nullable=True),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        sa.Column("received_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_logs_service", "logs", ["service"])
    op.create_index("ix_logs_level", "logs", ["level"])
    op.create_index("ix_logs_timestamp", "logs", ["timestamp"])
    op.create_index("ix_logs_service_ts", "logs", ["service", sa.text("timestamp DESC")])

    op.create_table(
        "metrics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("service", sa.String(128), nullable=False, server_default="default"),
        sa.Column("cpu", sa.Float, nullable=False),
        sa.Column("memory", sa.Float, nullable=False),
        sa.Column("latency", sa.Float, nullable=False),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        sa.Column("received_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_metrics_service", "metrics", ["service"])
    op.create_index("ix_metrics_timestamp", "metrics", ["timestamp"])

    op.create_table(
        "services",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("kind", sa.String(64), nullable=False, server_default="app"),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_services_name", "services", ["name"])

    op.create_table(
        "dependencies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.Integer,
            sa.ForeignKey("services.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(32), nullable=False, server_default="calls"),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("root_cause", sa.Text, nullable=False),
        sa.Column("suggestion", sa.Text, nullable=False),
        sa.Column("severity", sa.String(16), server_default="medium"),
        sa.Column("status", sa.String(16), server_default="open"),
        sa.Column("findings", sa.JSON, nullable=True),
        sa.Column("actions_taken", sa.JSON, nullable=True),
        sa.Column("feedback", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_incidents_service", "incidents", ["service"])
    op.create_index("ix_incidents_status", "incidents", ["status"])

    op.create_table(
        "incident_memory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "incident_id",
            sa.Integer,
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("incident_memory")
    op.drop_table("incidents")
    op.drop_table("dependencies")
    op.drop_table("services")
    op.drop_table("metrics")
    op.drop_table("logs")
    op.drop_table("users")
