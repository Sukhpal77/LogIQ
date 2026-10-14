import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from app.auth.security import encrypt, decrypt, totp, verify_totp
from app.billing import verify_event
from app.tenancy import tenant_scope, identity


def test_authenticator_reference_and_replay():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp(secret, 1) == "287082"
    assert verify_totp(secret, "287082", now=59) == 1
    assert verify_totp(secret, "287082", last_step=1, now=59) is None


def test_source_timezone_is_converted_before_storage():
    from app.ingestion.normalizer import _ts
    assert _ts("2026-10-06T12:00:00+05:30") == datetime(2026,10,6,6,30)
    assert _ts(datetime(2026,10,6,12,tzinfo=timezone(timedelta(hours=5,minutes=30)))) == datetime(2026,10,6,6,30)


def test_secrets_are_encrypted_and_reversible():
    raw = "https://example.test/private-secret"
    stored = encrypt(raw)
    assert raw not in stored
    assert decrypt(stored) == raw
    assert encrypt(raw) != stored


def test_payment_signature_tampering_and_age():
    raw = b'{"id":"evt_test","type":"invoice.paid"}'
    digest = hmac.new(b"secret", b"1000." + raw, hashlib.sha256).hexdigest()
    signature = "t=1000,v1=" + digest
    assert verify_event(raw, signature, "secret", now=1001)["id"] == "evt_test"
    for body, now in ((raw + b" ", 1001), (raw, 1400)):
        with pytest.raises(HTTPException):
            verify_event(body, signature, "secret", now=now)


def test_tenant_context_restores_after_exception():
    original = identity()
    with tenant_scope(42):
        assert identity() == 42
        with pytest.raises(RuntimeError):
            with tenant_scope(43):
                assert identity() == 43
                raise RuntimeError("abort")
        assert identity() == 42
    assert identity() == original


def test_database_filters_reads_updates_and_identical_event_ids():
    from sqlalchemy import create_engine, select, update, delete
    from sqlalchemy.orm import Session
    from app.db import models as m
    import app.db.session  # Installs isolation listeners on ORM sessions.
    engine = create_engine("sqlite://")
    m.Log.__table__.create(engine)
    with Session(engine) as session:
        for tenant in (12, 13):
            with tenant_scope(tenant):
                session.add(m.Log(event_id="same-id", service="store/production/api", level="info", message=str(tenant), attrs={}, timestamp=datetime.now(timezone.utc)))
                session.commit()
        with tenant_scope(12):
            assert [r.message for r in session.scalars(select(m.Log))] == ["12"]
            session.execute(update(m.Log).values(message="changed"))
            session.commit()
        with tenant_scope(13):
            assert [r.message for r in session.scalars(select(m.Log))] == ["13"]
            session.execute(delete(m.Log))
            session.commit()
        with tenant_scope(12):
            assert [r.message for r in session.scalars(select(m.Log))] == ["changed"]
            session.add(m.Log(tenant_id=13,event_id="bad",service="api",level="info",message="wrong",attrs={},timestamp=datetime.now(timezone.utc)))
            with pytest.raises(ValueError,match="Cross-customer"):
                session.flush()
            session.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_large_postgres_usage_sum_is_encoded_for_redis(monkeypatch):
    from contextlib import asynccontextmanager
    from decimal import Decimal
    from types import SimpleNamespace
    from app.ingestion import stream
    class Result:
        def scalar(self): return Decimal("8500000000")
    class Session:
        async def execute(self, *_): return Result()
    @asynccontextmanager
    async def scope(): yield Session()
    async def subscription(_):
        return SimpleNamespace(period_start=datetime.now(),period_end=datetime.now()), SimpleNamespace(limits={"ingestion_gb":10})
    class Redis:
        async def exists(self, _): return True
        async def eval(self, *args):
            assert args[-1] == 8500000000
            assert isinstance(args[-1], int)
            return "accepted"
    monkeypatch.setattr(stream,"session_scope",scope)
    monkeypatch.setattr(stream,"subscription",subscription)
    monkeypatch.setattr(stream,"get_redis",lambda:Redis())
    with tenant_scope(12):
        assert await stream.publish("logs",{"event_id":str(__import__('uuid').uuid4())}) == "accepted"
