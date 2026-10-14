"""ORM models for LOGIQ.

Tables:
  - logs              raw + normalized application logs
  - metrics           cpu/memory/latency time series
  - services          context graph nodes (a deployable unit)
  - dependencies      directed edges between services
  - incidents         persisted anomaly + AI analysis records
  - incident_memory   embeddings of past incidents (pgvector) for RAG
  - users             auth principals (admin / devops)
"""
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.tenancy import identity


class TenantRecord:
    tenant_id: Mapped[int] = mapped_column(Integer, default=identity, server_default="1", index=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="devops")  # admin | devops | viewer
    tenant_id: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    email: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    totp_secret: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    pending_totp_secret: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    last_totp_step: Mapped[int] = mapped_column(Integer, default=-1, server_default="-1")
    recovery_codes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Log(TenantRecord, Base):
    __tablename__ = "logs"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id", name="uq_logs_tenant_event"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    message: Mapped[str] = mapped_column(Text)
    attrs: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


Index("ix_logs_service_ts", Log.service, Log.timestamp.desc())


class Metric(TenantRecord, Base):
    __tablename__ = "metrics"
    __table_args__ = (UniqueConstraint("tenant_id", "event_id", name="uq_metrics_tenant_event"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    service: Mapped[str] = mapped_column(String(128), index=True, default="default")
    cpu: Mapped[float] = mapped_column(Float)
    memory: Mapped[float] = mapped_column(Float)
    latency: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Service(TenantRecord, Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_service_tenant_name"),)
    name: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="app")  # app | db | queue | cache | ext
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    out_edges = relationship(
        "Dependency", foreign_keys="Dependency.source_id", back_populates="source"
    )
    in_edges = relationship(
        "Dependency", foreign_keys="Dependency.target_id", back_populates="target"
    )


class Dependency(TenantRecord, Base):
    __tablename__ = "dependencies"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(String(32), default="calls")  # calls | reads | writes

    source = relationship("Service", foreign_keys=[source_id], back_populates="out_edges")
    target = relationship("Service", foreign_keys=[target_id], back_populates="in_edges")


class Incident(TenantRecord, Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    summary: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | resolved | dismissed
    findings: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    actions_taken: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class IncidentMemory(TenantRecord, Base):
    """Embedded summaries of past incidents — used for retrieval-augmented analysis."""

    __tablename__ = "incident_memory"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(Vector(1536))
    embedding_version: Mapped[str] = mapped_column(String(160), default="offline-hash-v1", server_default="legacy-unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class LogSummary(TenantRecord, Base):
    """Factual, redacted hourly/daily digests; retained within the customer's plan."""
    __tablename__ = "log_summaries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "service", "window_start", "window_end", name="uq_log_summary_window"),
        Index("ix_log_summaries_tenant_end", "tenant_id", "window_end"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), default="")
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    source_max_id: Mapped[int] = mapped_column(BigInteger, default=0)
    source_count: Mapped[int] = mapped_column(BigInteger, default=0)
    content: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotificationOutbox(TenantRecord, Base):
    __tablename__ = "notification_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[Optional[int]] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="active")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)  # INR paise
    limits: Mapped[dict] = mapped_column(JSON)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("name", "version"),)


class Subscription(TenantRecord, Base):
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(24), default="trial")
    period_end: Mapped[datetime] = mapped_column(DateTime)
    cancel_at_end: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    provider_id: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, server_default=func.now())
    grace_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id"),)


class Application(TenantRecord, Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    environment: Mapped[str] = mapped_column(String(32), default="production")
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("tenant_id", "name", "environment"),)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer, index=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"))
    name: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str] = mapped_column(String(24), default="ingest", server_default="ingest")


class RecurrenceInsight(TenantRecord, Base):
    __tablename__ = "recurrence_insights"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(24), default="active")
    feedback: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("tenant_id", "service", "fingerprint", name="uq_recurrence_pattern"), Index("ix_recurrence_tenant_updated", "tenant_id", "updated_at"))


class WorkspaceSetting(TenantRecord, Base):
    __tablename__ = "workspace_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Usage(TenantRecord, Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int] = mapped_column(BigInteger)
    reference: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Audit(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)



class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Invoice(TenantRecord, Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(128), unique=True)
    number: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuthChallenge(Base):
    __tablename__ = "auth_challenges"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class MailOutbox(Base):
    __tablename__ = "mail_outbox"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str] = mapped_column(String(128))
    encrypted_body: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
