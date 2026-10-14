from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from app.api import routes_insights as routes
from app.api.routes_analyze import _recall_similar
from app.ai.embeddings import Vector
from app.insights.history import normalize, redact, delta, digest, history_record
from app.insights.worker import save_window
from app.tenancy import tenant_scope


@pytest.mark.parametrize("message,secret", [
    ("password=very-private", "very-private"),
    ('{"api_key":"private-json-secret"}', "private-json-secret"),
    ("Authorization: Bearer private-bearer-token", "private-bearer-token"),
    ("Bearer private-bearer-token", "private-bearer-token"),
    ("postgres://demo:private-password@db:5432/app", "private-password"),
    ("Cookie: session=private-cookie; another=private-cookie", "private-cookie"),
    ("email=person@example.com", "person@example.com"),
])
def test_sensitive_values_are_redacted(message, secret):
    assert secret not in redact(message)
    assert secret not in normalize(message)


def test_fingerprints_preserve_error_codes_but_ignore_ids():
    assert normalize("HTTP 503 timeout request_id=abc123") == normalize("HTTP 503 timeout request_id=def456")
    assert normalize("HTTP 503 timeout") != normalize("HTTP 401 timeout")
    assert normalize("Failure 2026-10-06T10:00:00Z") == normalize("Failure 2026-10-07T11:30:00Z")


def test_comparisons_do_not_invent_percentages():
    assert delta(10, 0, True)["percent"] is None
    assert delta(10, 5, True)["percent"] == 100
    assert delta(10, 5, False)["previous"] is None


def test_confirmation_requires_cause_and_assessed_fix_requires_action():
    with pytest.raises(ValidationError):
        routes.ResolutionInput(confirmed=True)
    with pytest.raises(ValidationError):
        routes.ResolutionInput(fix_result="worked")
    assert not routes.ResolutionInput(status="resolved").confirmed


@pytest.mark.asyncio
async def test_resolution_preserves_ai_cause_and_reopen_timestamp(monkeypatch):
    now=datetime.utcnow()
    row=SimpleNamespace(id=8,service="checkout/production/api",summary="Timeout",root_cause="AI: network",reason="Analysis",suggestion="Check",severity="high",status="open",feedback="Existing feedback",resolution=None,created_at=now,resolved_at=None)
    session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda:row)),flush=AsyncMock())
    events=[]
    monkeypatch.setattr(routes,"audit",lambda s,u,a,data:events.append(data))
    body=routes.ResolutionInput(confirmed=True,confirmed_cause="Database pool exhausted password=private",action_taken="Reduced connection leak",fix_result="worked",status="resolved")
    result=await routes.resolution(8,body,session,SimpleNamespace(username="owner"))
    assert row.root_cause == "AI: network"
    assert row.resolution["confirmed"]
    assert "private" not in str(result)+str(events)
    assert row.resolved_at is not None
    await routes.resolution(8,body.model_copy(update={"status":"open","confirmed":False}),session,SimpleNamespace(username="owner"))
    assert row.resolved_at is None
    assert row.resolution["confirmed_at"] is None


@pytest.mark.asyncio
async def test_foreign_resolution_returns_not_found(monkeypatch):
    session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda:None)))
    with tenant_scope(42):
        with pytest.raises(HTTPException) as error:
            await routes.resolution(7,routes.ResolutionInput(),session,SimpleNamespace(username="owner"))
    assert error.value.status_code == 404
    assert 42 in session.execute.call_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_hash_vectors_use_confirmed_service_keyword_history():
    row=SimpleNamespace(id=4,summary="Database connection timeout",resolution={"confirmed":True,"confirmed_cause":"Connection pool exhausted"})
    session=SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda:SimpleNamespace(all=lambda:[row]))))
    result=await _recall_similar(session,Vector([0]*1536,"offline-hash-v1"),"checkout/production/api","Connection timeout")
    assert "keyword match" in result[0]
    assert "pool exhausted" in result[0]
    sql=str(session.execute.call_args.args[0])
    assert "l2_distance" not in sql
    assert "checkout/production/api" in session.execute.call_args.args[0].compile().params.values()


@pytest.mark.asyncio
async def test_no_hash_retrieval_without_relevant_keywords():
    session=SimpleNamespace(execute=AsyncMock())
    assert await _recall_similar(session,Vector([0]*1536,"offline-hash-v1"),"api","Everything normal") == []
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_digest_uses_full_counts_and_bounded_top_patterns():
    from app.insights import history
    start=datetime(2026,10,6,10);end=start+timedelta(hours=1)
    responses=iter([
        SimpleNamespace(all=lambda:[("info",10000),("error",100)]),
        SimpleNamespace(all=lambda:[("info",9000),("error",50)]),
        SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[{"pattern":"database timeout password=private","count":100,"evidence_id":7,"first_seen":start,"last_seen":end-timedelta(seconds=1)}])),
        SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[{"service":"api","logs":10100,"errors":100}])),
        SimpleNamespace(scalar=lambda:10100),
        SimpleNamespace(one=lambda:(start,end-timedelta(seconds=1))),
        SimpleNamespace(scalar=lambda:1),
        SimpleNamespace(mappings=lambda:SimpleNamespace(all=lambda:[{"time":start,"logs":10100,"errors":100}])),
    ])
    async def execute(query):return next(responses)
    result=await digest(SimpleNamespace(execute=execute),start,end,"api",start-timedelta(days=2))
    assert result["totals"]["total"]==10100
    assert result["totals"]["errors"]==100
    assert result["comparison"]["errors"]["percent"]==100
    assert "private" not in str(result)
    assert result["source_count"]==10100


@pytest.mark.asyncio
async def test_saved_digest_unchanged_does_not_duplicate():
    start=datetime(2026,10,6,10);end=start+timedelta(hours=1)
    row=SimpleNamespace(source_count=100,source_max_id=200,content={"version":"facts-v1","comparison":{"logs":{"previous":80},"errors":{"previous":2}}})
    responses=iter([SimpleNamespace(scalar_one_or_none=lambda:row),SimpleNamespace(one=lambda:(100,200)),SimpleNamespace(all=lambda:[("info",78),("error",2)])])
    statements=[]
    async def execute(statement):
        statements.append(statement);return next(responses)
    await save_window(SimpleNamespace(execute=execute),start,end,"api",start-timedelta(days=3))
    assert len(statements)==3


@pytest.mark.asyncio
async def test_digest_outside_retention_is_not_saved():
    session=SimpleNamespace(execute=AsyncMock())
    now=datetime.utcnow()
    await save_window(session,now-timedelta(days=4),now-timedelta(days=3),"api",now-timedelta(days=2))
    session.execute.assert_not_called()
