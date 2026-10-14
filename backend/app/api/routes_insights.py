"""Customer failure history, verified resolutions and factual log digests."""
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select, func, text
from app.auth.rbac import current_user, require_roles
from app.api.routes_workspace import audit
from app.db import models as m
from app.db.session import get_session
from app.insights.history import digest, history_record, redact, retention_days
from app.tenancy import identity
from app.insights import recurrence

router = APIRouter(tags=["history and insights"])
operator = require_roles("devops")


class ResolutionInput(BaseModel):
    category: Literal["unknown", "database", "capacity", "network", "deployment", "application", "dependency", "configuration"] = "unknown"
    component: str = Field(default="", max_length=128)
    deployment_version: str = Field(default="", max_length=128)
    confirmed_cause: str = Field(default="", max_length=2000)
    action_taken: str = Field(default="", max_length=2000)
    notes: str = Field(default="", max_length=4000)
    fix_result: Literal["unknown", "worked", "did_not_work", "prevented"] = "unknown"
    confirmed: bool = False
    status: Literal["open", "resolved", "dismissed"] = "open"
    evidence_ids: list[int] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def evidence_required(self):
        if self.confirmed and not self.confirmed_cause.strip():
            raise ValueError("enter the cause you verified before confirming it")
        if self.fix_result != "unknown" and not self.action_taken.strip():
            raise ValueError("record the action whose outcome you assessed")
        if self.evidence_ids and any(value <= 0 for value in self.evidence_ids):
            raise ValueError("evidence IDs must be positive")
        return self


@router.get("/workspace/insights/services")
async def services(session=Depends(get_session, scope="function"), user=Depends(current_user)):
    return (await session.execute(select(m.Service.name).where(m.Service.tenant_id == identity()).order_by(m.Service.name).limit(1000))).scalars().all()


@router.get("/workspace/insights/incidents")
async def incidents(service: str = Query("", max_length=128), status: Literal["open", "resolved", "dismissed"] | None = None, category: str = Query("", max_length=32), confirmed: bool = False, q: str = Query("", max_length=120), before_id: int | None = Query(None, gt=0), limit: int = Query(25, ge=1, le=100), session=Depends(get_session, scope="function"), user=Depends(current_user)):
    query = select(m.Incident).where(m.Incident.tenant_id == identity())
    if service:
        query = query.where(m.Incident.service == service)
    if status:
        query = query.where(m.Incident.status == status)
    if category:
        query = query.where(m.Incident.resolution["category"].as_string() == category)
    if confirmed:
        query = query.where(m.Incident.resolution["confirmed"].as_boolean().is_(True))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(m.Incident.summary.ilike("%"+escaped+"%", escape="\\") | m.Incident.resolution["confirmed_cause"].as_string().ilike("%"+escaped+"%", escape="\\"))
    if before_id:
        query = query.where(m.Incident.id < before_id)
    rows = (await session.execute(query.order_by(m.Incident.id.desc()).limit(limit+1))).scalars().all()
    return {"items": [history_record(row) for row in rows[:limit]], "next_cursor": rows[limit-1].id if len(rows)>limit else None}


@router.put("/workspace/insights/incidents/{incident_id}/resolution")
async def resolution(incident_id: int, body: ResolutionInput, session=Depends(get_session, scope="function"), user=Depends(operator)):
    row = (await session.execute(select(m.Incident).where(m.Incident.tenant_id == identity(), m.Incident.id == incident_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "incident not found")
    now = datetime.utcnow()
    config = {k: redact(v.strip()) if isinstance(v, str) else v for k, v in body.model_dump(exclude={"status", "evidence_ids"}).items()}
    if body.evidence_ids is not None:
        cutoff = now-timedelta(days=await retention_days(session))
        config["matching_evidence"] = await recurrence.selected_evidence(session, row, body.evidence_ids, cutoff, now)
    else:
        config["matching_evidence"] = (row.resolution or {}).get("matching_evidence", [])
    config.update({"confirmed_at": now.isoformat()+"Z" if body.confirmed else None, "confirmed_by": user.username if body.confirmed else None, "updated_at": now.isoformat()+"Z", "updated_by": user.username})
    row.resolution = config
    row.status = body.status
    if body.status in ("resolved", "dismissed"):
        row.resolved_at = row.resolved_at or now
    else:
        row.resolved_at = None
    audit(session, user, "incident.resolution", {"incident_id": row.id, "confirmed": body.confirmed, "fix_result": body.fix_result, "status": body.status})
    await session.flush()
    return {**history_record(row), "message": "Resolution saved. AI suggestions remain separate from your verified cause."}


@router.get("/workspace/insights/summary")
async def summary(service: str = Query("", max_length=128), minutes: int = Query(1440, ge=60, le=43200), session=Depends(get_session, scope="function"), user=Depends(current_user)):
    now = datetime.utcnow()
    days = await retention_days(session)
    retention_start = now-timedelta(days=days)
    requested_start = now-timedelta(minutes=minutes)
    start = max(requested_start, retention_start)
    await session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
    result = await digest(session, start, now, service, retention_start)
    result.update({"retention_days": days, "range_clipped": requested_start < retention_start, "requested_minutes": minutes})
    return result


@router.get("/workspace/insights/summaries")
async def summaries(service: str = Query("", max_length=128), interval: Literal["hour", "day"] = "hour", before_id: int | None = Query(None, gt=0), session=Depends(get_session, scope="function"), user=Depends(current_user)):
    days = await retention_days(session)
    seconds = 3600 if interval == "hour" else 86400
    query = select(m.LogSummary).where(m.LogSummary.tenant_id == identity(), m.LogSummary.service == service, m.LogSummary.window_start >= datetime.utcnow()-timedelta(days=days), func.extract("epoch", m.LogSummary.window_end-m.LogSummary.window_start) == seconds)
    if before_id:
        query = query.where(m.LogSummary.id < before_id)
    rows = (await session.execute(query.order_by(m.LogSummary.id.desc()).limit(21))).scalars().all()
    return {"items": [{"id": row.id, **row.content, "updated_at": row.updated_at.isoformat()+"Z"} for row in rows[:20]], "next_cursor": rows[19].id if len(rows)>20 else None, "retention_days": days}


@router.get("/workspace/insights/evidence/{log_id}")
async def evidence(log_id: int, session=Depends(get_session, scope="function"), user=Depends(current_user)):
    cutoff = datetime.utcnow()-timedelta(days=await retention_days(session))
    row = (await session.execute(select(m.Log).where(m.Log.tenant_id == identity(), m.Log.id == log_id, m.Log.timestamp >= cutoff))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "evidence is unavailable or has expired")
    return {"id": row.id, "service": row.service, "level": row.level, "message": redact(row.message)[:8192], "timestamp": row.timestamp.isoformat()+"Z"}


@router.post("/workspace/insights/summaries/{summary_id}/refresh")
async def refresh_summary(summary_id: int, session=Depends(get_session, scope="function"), user=Depends(operator)):
    days = await retention_days(session)
    cutoff = datetime.utcnow()-timedelta(days=days)
    row = (await session.execute(select(m.LogSummary).where(m.LogSummary.tenant_id == identity(), m.LogSummary.id == summary_id, m.LogSummary.window_start >= cutoff).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "digest is unavailable or has expired")
    await session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
    result = await digest(session, row.window_start, row.window_end, row.service, cutoff)
    row.content = result
    row.source_count, row.source_max_id, row.updated_at = result["source_count"], result["source_max_id"], datetime.utcnow()
    audit(session, user, "summary.refresh", {"summary_id": row.id})
    return {"id": row.id, **result, "updated_at": row.updated_at.isoformat()+"Z"}


@router.get("/workspace/insights/recurrence")
async def recurrence_warnings(service: str = Query("", max_length=128), before_id: int | None = Query(None, gt=0), session=Depends(get_session, scope="function"), user=Depends(current_user)):
    now = datetime.utcnow()
    cutoff = now-timedelta(days=await retention_days(session))
    query = select(m.RecurrenceInsight).where(m.RecurrenceInsight.tenant_id == identity(), m.RecurrenceInsight.updated_at >= cutoff)
    if service:
        query = query.where(m.RecurrenceInsight.service == service)
    if before_id:
        query = query.where(m.RecurrenceInsight.id < before_id)
    rows = (await session.execute(query.order_by(m.RecurrenceInsight.id.desc()).limit(21))).scalars().all()
    ids = {value for row in rows[:20] for value in row.content.get("matched_incident_ids", [])}
    incidents = (await session.execute(select(m.Incident).where(m.Incident.tenant_id == identity(), m.Incident.id.in_(ids)))).scalars().all() if ids else []
    items = [value for row in rows[:20] if (value := recurrence.public_record(row, incidents, now, cutoff))]
    return {"items": items, "next_cursor": rows[19].id if len(rows)>20 else None,
        "method": recurrence.METHOD, "window_minutes": recurrence.WINDOW_MINUTES, "fresh_minutes": recurrence.FRESH_MINUTES,
        "coverage": "Checks run approximately every five minutes. At least three matching errors across two separate minutes are required, with an event in the last ten minutes. Only explicitly linked evidence in a confirmed resolved incident is matched; the scan is bounded to 500 recent confirmed incidents and 100 sustained patterns. No match does not prove health or complete coverage."}


@router.post("/workspace/insights/recurrence/refresh")
async def refresh_recurrence(session=Depends(get_session, scope="function"), user=Depends(operator)):
    now = datetime.utcnow()
    await session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
    count = await recurrence.refresh(session, now, now-timedelta(days=await retention_days(session)))
    audit(session, user, "recurrence.refresh", {"active_patterns": count})
    return {"message": "Patterns checked. No notifications were sent.", "active_patterns": count}


class RecurrenceFeedback(BaseModel):
    outcome: Literal["useful", "incorrect", "resolved", "prevented", "not_assessed"]
    notes: str = Field(default="", max_length=2000)


@router.put("/workspace/insights/recurrence/{insight_id}/feedback")
async def recurrence_feedback(insight_id: int, body: RecurrenceFeedback, session=Depends(get_session, scope="function"), user=Depends(operator)):
    cutoff = datetime.utcnow()-timedelta(days=await retention_days(session))
    row = (await session.execute(select(m.RecurrenceInsight).where(m.RecurrenceInsight.tenant_id == identity(), m.RecurrenceInsight.id == insight_id, m.RecurrenceInsight.updated_at >= cutoff).with_for_update())).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "warning is unavailable or has expired")
    row.feedback = {"outcome": body.outcome, "notes": redact(body.notes.strip()), "by": user.username, "at": datetime.utcnow().isoformat()+"Z"}
    audit(session, user, "recurrence.feedback", {"insight_id": insight_id, "outcome": body.outcome})
    return {"message": "Feedback saved. This assessment does not change the observed evidence."}
