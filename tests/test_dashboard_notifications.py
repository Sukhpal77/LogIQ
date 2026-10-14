from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from app.api import routes_notification_config as delivery
from app.api import routes_dashboards as dashboards
from app.api import routes_workspace as workspace
from app.auth.security import decrypt
from app.config import settings
from app.db import models as m
from app.tenancy import tenant_scope


def test_dashboard_limits_and_private_query():
    with pytest.raises(ValidationError):
        dashboards.Dashboard(name="invalid", widgets=[dashboards.Widget(id="w",title="CPU")] * 25)
    with pytest.raises(ValidationError):
        dashboards.Widget(id="w", title="Unsafe", color="url(javascript:x)")
    with pytest.raises(ValidationError):
        dashboards.Dashboard(name="invalid", minutes=50000)
    with tenant_scope(42):
        query = dashboards.owned(SimpleNamespace(username="alice"))
        compiled = query.compile(dialect=postgresql.dialect())
        assert 42 in compiled.params.values()
        assert "alice" in compiled.params.values()
        assert "dashboard" in compiled.params.values()


@pytest.mark.asyncio
async def test_foreign_personal_dashboard_cannot_be_changed():
    class Result:
        def scalar_one_or_none(self): return None
    session=SimpleNamespace(execute=AsyncMock(return_value=Result()))
    with pytest.raises(HTTPException) as error:
        await dashboards.update(77, dashboards.Dashboard(name="Other person's layout"), session, SimpleNamespace(username="alice"))
    assert error.value.status_code == 404


@pytest.mark.parametrize("config,service,severity,expected", [
    ({},"checkout","medium",True),
    ({"service":"checkout","minimum_severity":"high"},"checkout","critical",True),
    ({"service":"checkout"},"billing","critical",False),
    ({"minimum_severity":"high"},"checkout","medium",False),
    ({"minimum_severity":"critical"},"checkout","high",False),
])
def test_notification_filters(config,service,severity,expected):
    assert delivery.matches(config,SimpleNamespace(service=service,severity=severity)) is expected


@pytest.mark.asyncio
async def test_smtp_secret_is_encrypted_preserved_and_cleared(monkeypatch):
    for key in ("smtp_host","smtp_port","smtp_user","smtp_password","smtp_sender","notification_hosts"):
        monkeypatch.setattr(settings,key,getattr(settings,key))
    row=m.WorkspaceSetting(kind="notification_platform",name="Delivery",config={})
    class Result:
        def scalar_one_or_none(self): return row
    session=SimpleNamespace(execute=AsyncMock(return_value=Result()),commit=AsyncMock())
    audits=[]
    monkeypatch.setattr(delivery,"audit",lambda s,u,a,detail:audits.append(detail))
    body=delivery.DeliverySettings(smtp_host="smtp.example.com",smtp_sender="alerts@example.com",smtp_password="private-smtp-secret")
    result=await delivery.save_settings(body,session,SimpleNamespace(username="admin"))
    assert decrypt(row.config["password_encrypted"])=="private-smtp-secret"
    assert "private-smtp-secret" not in str(result)+str(audits)
    cipher=row.config["password_encrypted"]
    await delivery.save_settings(body.model_copy(update={"smtp_password":""}),session,SimpleNamespace(username="admin"))
    assert row.config["password_encrypted"]==cipher
    await delivery.save_settings(body.model_copy(update={"smtp_password":"","clear_password":True}),session,SimpleNamespace(username="admin"))
    assert not settings.smtp_password


@pytest.mark.asyncio
async def test_saving_destination_does_not_send_and_edit_keeps_secret(monkeypatch):
    from app.actions import delivery as transport
    monkeypatch.setattr(delivery,"load_delivery_settings",AsyncMock())
    monkeypatch.setattr(transport,"deliver",AsyncMock())
    body=workspace.SettingInput(kind="notification",name="Slack",config={"channel":"slack","url":"https://hooks.slack.com/services/test","service":"checkout","minimum_severity":"high"},enabled=False)
    session=SimpleNamespace()
    await workspace.prepare_setting(body,session)
    assert "url" not in body.config
    assert decrypt(body.config["encrypted_url"])=="https://hooks.slack.com/services/test"
    existing=SimpleNamespace(config=body.config)
    edited=workspace.SettingInput(kind="notification",name="Slack edit",config={"channel":"slack","url":"","minimum_severity":"medium"},enabled=True)
    await workspace.prepare_setting(edited,session,existing)
    assert edited.config["encrypted_url"]==body.config["encrypted_url"]
    transport.deliver.assert_not_called()


def test_removed_env_notification_settings_are_not_loaded(monkeypatch):
    monkeypatch.setenv("SMTP_PASSWORD","must-not-be-loaded")
    from app.config import Settings
    assert Settings(_env_file=None).smtp_password==""


@pytest.mark.asyncio
async def test_smtp_connection_failure_hides_credentials(monkeypatch):
    from app.actions import mail
    monkeypatch.setattr(delivery, "load_delivery_settings", AsyncMock())
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_sender", "alerts@example.com")
    def fail():
        raise RuntimeError("private-password-in-provider-error")
    monkeypatch.setattr(mail, "check_connection", fail)
    monkeypatch.setattr(delivery, "audit", lambda *args: None)
    result = await delivery.test_connection(SimpleNamespace(), SimpleNamespace(username="admin"))
    assert not result["success"]
    assert "private-password" not in str(result)
    assert "No email was sent" in result["status"]


@pytest.mark.asyncio
async def test_notification_worker_runs_without_ai(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from app import monitor
    from app.actions import mail
    calls=[]
    class Connection:
        async def execute(self, statement):
            return SimpleNamespace(scalar=lambda: True)
    @asynccontextmanager
    async def connect():
        yield Connection()
    monkeypatch.setattr(monitor, "engine", SimpleNamespace(connect=connect))
    async def config(): calls.append("config")
    async def notifications(): calls.append("alerts")
    async def drain(): calls.append("mail")
    async def stop(seconds):
        assert seconds==15
        raise asyncio.CancelledError()
    monkeypatch.setattr(delivery,"load_delivery_settings",config)
    monkeypatch.setattr(monitor,"notifications",notifications)
    monkeypatch.setattr(mail,"drain",drain)
    monkeypatch.setattr(monitor.asyncio,"sleep",stop)
    monkeypatch.setattr(monitor,"cycle",AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await monitor.run_notifications()
    assert calls==["config","alerts","mail"]
    monitor.cycle.assert_not_called()
