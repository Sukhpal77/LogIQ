from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app.config import settings, get_settings
from app.auth.rbac import ingest_user, current_user
from app.ingestion import worker
from app.main import app
from app.api import routes_logs, routes_metrics
from app.auth.jwt import hash_password, verify_password
from app.ingestion.normalizer import normalize_log


def test_ingest_key_is_not_a_dashboard_login(monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_key", "a" * 32)
    assert ingest_user("Bearer " + "a" * 32).role == "ingest"
    with pytest.raises(HTTPException):
        current_user("Bearer " + "a" * 32)


def test_bulk_metrics_and_fluent_records(monkeypatch):
    monkeypatch.setattr(settings, "ingest_api_key", "a" * 32)
    metric = AsyncMock(return_value="1-0")
    logs = AsyncMock(return_value="2-0")
    monkeypatch.setattr(routes_metrics, "publish_metric", metric)
    monkeypatch.setattr(routes_logs, "publish_log", logs)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + "a" * 32}
    response = client.post("/metrics/bulk", headers=headers, json=[{
        "service": "checkout", "cpu": 30, "memory": 40, "latency": 100}])
    assert response.status_code == 202
    assert client.post("/ingest/logs", headers=headers, json=[{
        "log": "connection failed", "severity": "err", "service": "checkout"}]).status_code == 202
    assert logs.await_args.args[0]["level"] == "error"
    assert client.get("/logs", headers=headers).status_code == 401
    assert client.post("/metrics/bulk", headers=headers, json=[]).status_code == 413


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_worker_only_acknowledges_after_commit(monkeypatch, fail):
    order = []
    class Redis:
        async def xack(self, *args):
            order.append("ack")
        async def xdel(self, *args):
            order.append("delete")
    class Session:
        async def execute(self, statement):
            order.append("insert")
            assert "ON CONFLICT" in str(statement)
    @asynccontextmanager
    async def scope():
        yield Session()
        if fail:
            raise RuntimeError("database commit failed")
        order.append("commit")
    monkeypatch.setattr(worker, "session_scope", scope)
    monkeypatch.setattr(worker, "get_redis", lambda: Redis())
    record = {"service": "checkout", "level": "error", "message": "failed"}
    entries = [("1-0", {"data": json.dumps(record)})]
    if fail:
        with pytest.raises(RuntimeError):
            await worker._persist(settings.log_stream, entries)
        assert order == ["insert"]
    else:
        assert await worker._persist(settings.log_stream, entries) == 1
        assert order == ["insert", "commit", "ack", "delete"]


def test_production_rejects_defaults(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", "change-me")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="JWT_SECRET"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_password_hash_roundtrip():
    hashed = hash_password("example-password-123")
    assert verify_password("example-password-123", hashed)
    assert not verify_password("wrong-password", hashed)


def test_log_metadata_preserves_trace_id():
    log = normalize_log({"service": "demo", "level": "error", "message": "failed",
                         "attrs": {"trace_id": "request-123"}, "host": "demo-host"})
    assert log.attrs == {"trace_id": "request-123", "host": "demo-host"}
