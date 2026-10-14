"""Transparent exact-pattern recurrence checks, without AI or forecasting."""
import hashlib
from datetime import datetime, timedelta
from sqlalchemy import select, func, delete, text
from sqlalchemy.dialects.postgresql import insert
from app.db import models as m
from app.tenancy import identity
from app.insights.history import sql_pattern, ERROR_LEVELS, history_record

METHOD = "exact-pattern-v1"
WINDOW_MINUTES = 60
FRESH_MINUTES = 10


def fingerprint(pattern):
    return hashlib.sha256(pattern.encode()).hexdigest()


def sustained(count, minutes, latest, now):
    return count >= 3 and minutes >= 2 and now-timedelta(minutes=FRESH_MINUTES) <= latest <= now


async def selected_evidence(session, incident, ids, cutoff, now):
    """Customers explicitly associate source events with a verified failure."""
    if not ids:
        return []
    end = min(incident.resolved_at or now, now)
    pattern = sql_pattern()
    rows = (await session.execute(select(m.Log.id, pattern.label("pattern"), m.Log.timestamp).where(
        m.Log.tenant_id == identity(), m.Log.service == incident.service,
        m.Log.id.in_(set(ids)), func.lower(m.Log.level).in_(ERROR_LEVELS),
        m.Log.timestamp >= max(cutoff, incident.created_at-timedelta(hours=1)),
        m.Log.timestamp <= end))).mappings().all()
    if len(rows) != len(set(ids)):
        from fastapi import HTTPException
        raise HTTPException(422, "Select retained error evidence from this service during the incident. Evidence may be unavailable, outside the incident period or in another workspace.")
    return [{"id": r["id"], "fingerprint": fingerprint(r["pattern"]), "observed_at": r["timestamp"].isoformat()+"Z", "method": METHOD} for r in rows]


def matching_history(incidents, service, key, start, cutoff):
    result = []
    for incident in incidents:
        resolution = incident.resolution or {}
        if incident.service != service or incident.status != "resolved" or not resolution.get("confirmed") or not resolution.get("confirmed_cause") or not incident.resolved_at or not cutoff <= incident.resolved_at < start:
            continue
        evidence = [item for item in resolution.get("matching_evidence", []) if item.get("method") == METHOD and item.get("fingerprint") == key and item.get("observed_at", "") >= cutoff.isoformat()+"Z"]
        if evidence:
            result.append(incident)
    return result[:3]


async def refresh(session, now, cutoff):
    start = max(now-timedelta(minutes=WINDOW_MINUTES), cutoff)
    await session.execute(delete(m.RecurrenceInsight).where(m.RecurrenceInsight.tenant_id == identity(), m.RecurrenceInsight.updated_at < cutoff))
    # Fingerprints are derived customer data; prune them with source retention.
    await session.execute(text("""UPDATE incidents SET resolution =
        (resolution::jsonb || jsonb_build_object('matching_evidence',
            (SELECT coalesce(jsonb_agg(item), '[]'::jsonb)
             FROM jsonb_array_elements(resolution::jsonb->'matching_evidence') item
             WHERE item->>'observed_at' >= :cutoff)))::json
        WHERE tenant_id = :tenant AND jsonb_typeof(resolution::jsonb->'matching_evidence') = 'array'
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(resolution::jsonb->'matching_evidence') item
                      WHERE item->>'observed_at' < :cutoff)
        """), {"tenant": identity(), "cutoff": cutoff.isoformat()+"Z"})
    incidents = (await session.execute(select(m.Incident).where(
        m.Incident.tenant_id == identity(), m.Incident.status == "resolved",
        m.Incident.resolved_at >= cutoff, m.Incident.resolved_at < start,
        m.Incident.resolution["confirmed"].as_boolean().is_(True)
    ).order_by(m.Incident.resolved_at.desc()).limit(500))).scalars().all()
    pattern = sql_pattern()
    minute = func.date_trunc("minute", m.Log.timestamp)
    rows = (await session.execute(select(m.Log.service, pattern.label("pattern"), func.count().label("count"),
        func.count(func.distinct(minute)).label("minutes"), func.min(m.Log.timestamp).label("first"),
        func.max(m.Log.timestamp).label("last"), func.max(m.Log.id).label("evidence_id")
    ).where(m.Log.tenant_id == identity(), m.Log.timestamp >= start, m.Log.timestamp < now,
        func.lower(m.Log.level).in_(ERROR_LEVELS)
    ).group_by(m.Log.service, pattern).having(func.count() >= 3,
        func.count(func.distinct(minute)) >= 2,
        func.max(m.Log.timestamp) >= now-timedelta(minutes=FRESH_MINUTES)
    ).order_by(func.count().desc(), m.Log.service, pattern).limit(100))).mappings().all()
    previous = (await session.execute(select(m.RecurrenceInsight).where(m.RecurrenceInsight.tenant_id == identity(), m.RecurrenceInsight.state == "active"))).scalars().all()
    active = set()
    for row in rows:
        if not sustained(row["count"], row["minutes"], row["last"], now):
            continue
        key = fingerprint(row["pattern"])
        matches = matching_history(incidents, row["service"], key, start, cutoff)
        if not matches:
            continue
        active.add((row["service"], key))
        content = {"method": METHOD, "kind": "recurring_fault", "pattern": row["pattern"][:600],
            "count": row["count"], "observed_minutes": row["minutes"], "evidence_id": row["evidence_id"],
            "first_seen": row["first"].isoformat()+"Z", "last_seen": row["last"].isoformat()+"Z",
            "window_start": start.isoformat()+"Z", "window_end": now.isoformat()+"Z",
            "matched_incident_ids": [incident.id for incident in matches]}
        statement = insert(m.RecurrenceInsight).values(tenant_id=identity(), service=row["service"], fingerprint=key,
            content=content, state="active", feedback={}, created_at=now, updated_at=now,
            expires_at=row["last"]+timedelta(minutes=FRESH_MINUTES))
        await session.execute(statement.on_conflict_do_update(constraint="uq_recurrence_pattern",
            set_={field: statement.excluded[field] for field in ("content", "state", "updated_at", "expires_at")}))
    for row in previous:
        if (row.service, row.fingerprint) not in active:
            row.state = "not_observed"
            row.expires_at = now
    return len(active)


def public_record(row, incidents, now, cutoff):
    content = row.content
    start = datetime.fromisoformat(content["window_start"].replace("Z", ""))
    matches = matching_history(incidents, row.service, row.fingerprint, start, cutoff)
    ids = set(content.get("matched_incident_ids", []))
    matches = [item for item in matches if item.id in ids]
    if not matches:
        return None
    state = row.state if row.expires_at > now else "not_observed"
    return {"id": row.id, "service": row.service, **content, "state": state, "feedback": row.feedback,
        "created_at": row.created_at.isoformat()+"Z", "updated_at": row.updated_at.isoformat()+"Z", "expires_at": row.expires_at.isoformat()+"Z",
        "matches": [history_record(item) for item in matches],
        "similarities": ["Exact normalized error pattern", "Same application, environment and service", "Customer-linked evidence in a confirmed resolved incident"],
        "limitations": ["This error is already occurring; this is not a prediction of a future crash.",
            "Matching text does not verify that the current root cause is the same.",
            "Current component, deployment version and causal resource measurements have not been verified.",
            "No recent match may mean missing collection; it does not prove recovery."]}
