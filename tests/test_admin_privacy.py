from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from starlette.requests import Request
from fastapi import HTTPException
from app.auth.middleware import workspace_middleware
from app.api import routes_workspace as routes
from app.api.routes_identity import preview
from app.auth.security import decrypt
from app.config import settings
from app.db import models as m
from contextlib import asynccontextmanager


@pytest.mark.asyncio
async def test_workspace_override_rejected_without_reading_customer_data():
    request = Request({'type':'http', 'method':'GET', 'path':'/logs', 'headers':[(b'x-workspace-id',b'42')], 'scheme':'http', 'server':('localhost',8000), 'query_string':b''})
    next_handler = AsyncMock()
    response = await workspace_middleware(request, next_handler)
    assert response.status_code == 403
    next_handler.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/logs','/metrics','/incidents','/workspace/logs','/workspace/applications','/workspace/dashboards','/workspace/dashboard-data','/workspace/insights/incidents','/workspace/insights/summary','/workspace/insights/evidence/1','/workspace/insights/recurrence','/workspace/insights/recurrence/refresh','/analyze','/action'])
async def test_authenticated_platform_admin_cannot_use_customer_data_routes(monkeypatch, path):
    from app.auth import middleware
    user=SimpleNamespace(username='admin',tenant_id=1,session_version=0,role='admin',owner=True)
    class Result:
        def scalar_one_or_none(self): return user
    session=SimpleNamespace(execute=AsyncMock(return_value=Result()),get=AsyncMock(return_value=SimpleNamespace(status='active')))
    @asynccontextmanager
    async def scope(): yield session
    monkeypatch.setattr(middleware,'session_scope',scope)
    monkeypatch.setattr(middleware,'decode_token',lambda _: {'sub':'admin','tenant_id':1,'sv':0})
    request=Request({'type':'http','method':'GET','path':path,'headers':[(b'authorization',b'Bearer test-session')],'scheme':'http','server':('localhost',8000),'query_string':b''})
    next_handler=AsyncMock()
    response=await middleware.workspace_middleware(request,next_handler)
    assert response.status_code==403
    next_handler.assert_not_called()


@pytest.mark.asyncio
async def test_admin_cannot_preview_private_reset_emails():
    with pytest.raises(HTTPException) as error:
        await preview(1, AsyncMock(), SimpleNamespace(role='admin'))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_key_update_encrypts_preserves_and_never_returns_secret(monkeypatch):
    monkeypatch.setattr(settings, 'gemini_api_key', 'old-key')
    monkeypatch.setattr(settings, 'llm_provider', 'gemini')
    monkeypatch.setattr(settings, 'gemini_model', settings.gemini_model)
    monkeypatch.setattr(settings, 'openai_model', settings.openai_model)
    row = m.WorkspaceSetting(kind='llm', name='AI', config={}, enabled=True, tenant_id=1)
    class Result:
        def scalar_one_or_none(self): return row
    session = SimpleNamespace(execute=AsyncMock(return_value=Result()), commit=AsyncMock())
    audits = []
    monkeypatch.setattr(routes, 'audit', lambda _s, _u, _a, details: audits.append(details))
    body = routes.LlmInput(provider='gemini', openai_model='test-openai', gemini_model='test-gemini', gemini_api_key='new-private-key')
    result = await routes.update_llm(body, session, SimpleNamespace(username='admin'))
    assert decrypt(row.config['gemini_api_key_encrypted']) == 'new-private-key'
    assert settings.gemini_api_key == 'new-private-key'
    assert 'new-private-key' not in str(result) + str(audits) + str(routes.serialize(row))
    saved_cipher = row.config['gemini_api_key_encrypted']
    await routes.update_llm(body.model_copy(update={'gemini_api_key':''}), session, SimpleNamespace(username='admin'))
    assert settings.gemini_api_key == 'new-private-key'
    assert row.config['gemini_api_key_encrypted'] == saved_cipher
