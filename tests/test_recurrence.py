from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from app.insights import recurrence as r
from app.insights.history import normalize
from app.api import routes_insights as routes
from app.tenancy import tenant_scope

NOW = datetime(2026, 10, 7, 12)


def incident(service="shop/production/api", confirmed=True, status="resolved", closed=None, observed=None):
    return NS(id=7, service=service, status=status, resolved_at=closed or NOW-timedelta(hours=2),
        resolution={"confirmed": confirmed, "confirmed_cause": "Pool exhausted", "matching_evidence": [{"method":r.METHOD, "fingerprint":r.fingerprint(normalize("HTTP 503 timeout")), "observed_at":(observed or NOW-timedelta(hours=3)).isoformat()+"Z"}]})


def test_requires_repetition_independent_minutes_and_freshness():
    assert r.sustained(3, 2, NOW-timedelta(minutes=1), NOW)
    assert not r.sustained(2, 2, NOW, NOW)
    assert not r.sustained(30, 1, NOW, NOW)
    assert not r.sustained(3, 2, NOW-timedelta(minutes=11), NOW)
    assert not r.sustained(3, 2, NOW+timedelta(minutes=1), NOW)


@pytest.mark.parametrize("change", [
    {"service":"shop/staging/api"}, {"confirmed":False}, {"status":"open"},
    {"closed":NOW-timedelta(minutes=20)}, {"observed":NOW-timedelta(days=4)}
])
def test_unrelated_unconfirmed_ongoing_and_expired_history_excluded(change):
    assert r.matching_history([incident(**change)], "shop/production/api",r.fingerprint(normalize("HTTP 503 timeout")),NOW-timedelta(hours=1),NOW-timedelta(days=3)) == []


def test_exact_matching_keeps_http_codes_separate():
    records=[incident()]
    assert r.matching_history(records,"shop/production/api",r.fingerprint(normalize("HTTP 503 timeout")),NOW-timedelta(hours=1),NOW-timedelta(days=3)) == records
    assert not r.matching_history(records,"shop/production/api",r.fingerprint(normalize("HTTP 401 timeout")),NOW-timedelta(hours=1),NOW-timedelta(days=3))
    records[0].resolution["matching_evidence"][0]["method"]="old-version"
    assert not r.matching_history(records,"shop/production/api",r.fingerprint(normalize("HTTP 503 timeout")),NOW-timedelta(hours=1),NOW-timedelta(days=3))


@pytest.mark.asyncio
async def test_invalid_or_foreign_evidence_cannot_be_linked():
    s=NS(execute=AsyncMock(return_value=NS(mappings=lambda:NS(all=lambda:[]))))
    row=incident();row.created_at=NOW-timedelta(hours=3)
    with tenant_scope(42):
        with pytest.raises(HTTPException) as error:
            await r.selected_evidence(s,row,[9],NOW-timedelta(days=3),NOW)
    assert error.value.status_code == 422
    params=s.execute.call_args.args[0].compile().params
    assert 42 in params.values() and row.service in params.values()


@pytest.mark.asyncio
async def test_selected_evidence_has_no_raw_message_and_duplicates_are_safe():
    pattern=normalize("HTTP 503 timeout request_id=abc password=private")
    s=NS(execute=AsyncMock(return_value=NS(mappings=lambda:NS(all=lambda:[{"id":9,"pattern":pattern,"timestamp":NOW-timedelta(hours=3)}]))))
    row=incident();row.created_at=NOW-timedelta(hours=3)
    result=await r.selected_evidence(s,row,[9,9],NOW-timedelta(days=3),NOW)
    assert len(result)==1 and "private" not in str(result)
    assert result[0]["fingerprint"] == r.fingerprint(pattern)


@pytest.mark.asyncio
async def test_foreign_warning_feedback_is_not_found():
    from app.insights import history
    s=NS(execute=AsyncMock(side_effect=[NS(scalar_one_or_none=lambda:{"retention_days":3}),NS(scalar_one_or_none=lambda:None)]))
    with tenant_scope(88),pytest.raises(HTTPException) as error:
        await routes.recurrence_feedback(9,routes.RecurrenceFeedback(outcome="useful"),s,NS(username="owner"))
    assert error.value.status_code == 404
    assert 88 in s.execute.call_args.args[0].compile().params.values()


def test_expired_warnings_do_not_claim_recovery_and_confirmation_revocation_hides_them():
    old=incident()
    old.summary="Timeout";old.root_cause="AI suggestion";old.reason="";old.suggestion="";old.severity="high";old.feedback="";old.created_at=NOW-timedelta(hours=3)
    row=NS(id=5,service=old.service,fingerprint=r.fingerprint(normalize("HTTP 503 timeout")),content={"window_start":(NOW-timedelta(hours=1)).isoformat()+"Z","matched_incident_ids":[7]},state="active",feedback={},created_at=NOW-timedelta(minutes=20),updated_at=NOW-timedelta(minutes=15),expires_at=NOW-timedelta(minutes=5))
    result=r.public_record(row,[old],NOW,NOW-timedelta(days=3))
    assert result["state"]=="not_observed"
    assert any("not a prediction" in value for value in result["limitations"])
    old.resolution["confirmed"]=False
    assert r.public_record(row,[old],NOW,NOW-timedelta(days=3)) is None
