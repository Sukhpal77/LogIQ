from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock
import pytest
from app.api import routes_analyze as route
from app.db import models
from app.config import settings
from app import monitor
from app.models.schemas import ActionResult


@pytest.mark.asyncio
async def test_repeated_analysis_reuses_incident_and_one_notification(monkeypatch):
    monkeypatch.setattr(route, "_recent_metrics", AsyncMock(return_value=[]))
    monkeypatch.setattr(route, "_recent_error_logs", AsyncMock(return_value=[]))
    monkeypatch.setattr(route, "run_detection", lambda _: (True, [], "high"))
    monkeypatch.setattr(route, "neighborhood", AsyncMock(return_value={"upstream": [], "downstream": []}))
    monkeypatch.setattr(route, "_recall_similar", AsyncMock(return_value=[]))
    monkeypatch.setattr(route, "embed", lambda _: [0.0] * 1536)
    monkeypatch.setattr(route.agents, "run", lambda state: {**state, "analysis": "High CPU", "root_cause": "Load", "suggestion": "Inspect", "actions": ["scale:checkout"]})
    class Session:
        incident = None
        added = []
        async def execute(self, statement, *args):
            session = self
            class Result:
                def first(self):
                    return (1,)
                def scalar_one_or_none(self):
                    return session.incident
            return Result()
        def add(self, item):
            self.added.append(item)
            if isinstance(item, models.Incident):
                self.incident = item
                item.id = 7
        async def flush(self):
            pass
        async def commit(self):
            pass
    session = Session()
    first = await route.analyze(service="checkout", session=session, _user=None)
    second = await route.analyze(service="checkout", session=session, _user=None)
    assert first.incident_id == second.incident_id == 7
    assert sum(isinstance(item, models.Incident) for item in session.added) == 1
    assert sum(isinstance(item, models.NotificationOutbox) for item in session.added) == 1
    assert sum(isinstance(item, models.IncidentMemory) for item in session.added) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [False, True])
async def test_notification_retry_is_persisted(monkeypatch, success):
    item = models.NotificationOutbox(tenant_id=1, incident_id=7, text="alert", attempts=0, next_attempt=datetime.utcnow())
    incident = models.Incident(id=7, service="checkout", severity="high", actions_taken={"items": []})
    class Session:
        calls = 0
        async def execute(self, _):
            self.calls += 1
            chosen = item if self.calls == 1 else None
            class Result:
                def scalar_one_or_none(self):
                    return chosen
                def scalars(self):
                    return self
                def all(self):
                    return [models.WorkspaceSetting(config={"channel":"slack"})]
            return Result()
        async def get(self, *_, **kwargs):
            return incident
    session = Session()
    @asynccontextmanager
    async def scope():
        yield session
    monkeypatch.setattr(monitor, "session_scope", scope)
    from app.actions import delivery
    monkeypatch.setattr(delivery, "deliver", AsyncMock(return_value={"success":success,"detail":"test"}))
    before = item.next_attempt
    await monitor.notifications()
    assert item.attempts == 1
    if success:
        assert item.sent_at is not None
        assert incident.actions_taken["items"][0]["success"]
    else:
        assert item.sent_at is None
        assert item.next_attempt > before
