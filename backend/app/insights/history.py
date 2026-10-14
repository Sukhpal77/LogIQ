"""Factual summaries built from full database counts, with bounded evidence."""
import hashlib
import re
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.db import models as m
from app.tenancy import identity

VERSION = "facts-v1"
ERROR_LEVELS = ("error", "critical", "fatal")
# PostgreSQL ARE and Python-compatible expressions. Redact before fingerprints,
# saved digests or incident memory are produced.
REDACTIONS = (
    (r'((?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+', r'\1[redacted]'),
    (r'(bearer\s+)[a-z0-9._~+/=-]+', r'\1[redacted]'),
    (r'("?(?:password|passwd|pwd|token|secret|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|cookie|set-cookie)"?\s*[:=]\s*)(?:"[^"\r\n]*"|\x27[^\x27\r\n]*\x27|[^,;\s]+)', r'\1[redacted]'),
    (r'([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@', r'\1[redacted]@'),
    (r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}', '[email]'),
    (r'\m(?:sk-|AIza|lq_)[a-z0-9_-]{16,}', '[credential]'),
)
VARIABLES = (
    (r'[0-9]{4}-[0-9]{2}-[0-9]{2}[t ][0-9:.+-]+z?', '[time]'),
    (r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '[id]'),
    (r'\m[0-9]{1,3}(?:\.[0-9]{1,3}){3}\M', '[address]'),
    (r'((?:request[_-]?id|trace[_-]?id|event[_-]?id)\s*[:=]\s*)[a-z0-9_-]+', r'\1[id]'),
    (r'\m[0-9]{4,}\M', '[number]'),
)


def python_pattern(pattern):
    return pattern.replace(r'\m', r'\b').replace(r'\M', r'\b')


def redact(value):
    value = str(value or "")
    for pattern, replacement in REDACTIONS:
        value = re.sub(python_pattern(pattern), replacement, value, flags=re.I)
    return value


def normalize(value):
    value = redact(value).lower()
    for pattern, replacement in VARIABLES:
        value = re.sub(python_pattern(pattern), replacement, value, flags=re.I)
    return re.sub(r'\s+', ' ', value).strip()[:8192]


def sql_pattern():
    message = func.lower(func.left(m.Log.message, 8192))
    for pattern, replacement in (*REDACTIONS, *VARIABLES):
        message = func.regexp_replace(message, pattern, replacement, "gi")
    return func.trim(func.regexp_replace(message, r'\s+', ' ', 'g'))


def scope(statement, start, end, service=""):
    statement = statement.where(m.Log.tenant_id == identity(), m.Log.timestamp >= start, m.Log.timestamp < end)
    return statement.where(m.Log.service == service) if service else statement


async def counts(session, start, end, service=""):
    rows = (await session.execute(scope(select(func.lower(m.Log.level), func.count()), start, end, service).group_by(func.lower(m.Log.level)))).all()
    levels = dict(rows)
    return {"total": sum(levels.values()), "errors": sum(levels.get(level, 0) for level in ERROR_LEVELS), "warnings": levels.get("warning", 0) + levels.get("warn", 0), "levels": levels}


def delta(current, previous, comparable):
    if not comparable:
        return {"previous": None, "difference": None, "percent": None, "description": "Previous full period is outside retained history."}
    difference = current - previous
    percent = round(difference / previous * 100, 1) if previous else None
    return {"previous": previous, "difference": difference, "percent": percent, "description": "No baseline events; percentage change is unavailable." if not previous else "Compared with the preceding period of equal length."}


async def digest(session, start, end, service="", retention_start=None):
    current = await counts(session, start, end, service)
    duration = end - start
    previous_start = start - duration
    comparable = retention_start is None or previous_start >= retention_start
    previous = await counts(session, previous_start, start, service) if comparable else {"total": 0, "errors": 0}
    pattern = sql_pattern()
    top = (await session.execute(scope(select(pattern.label("pattern"), func.count().label("count"), func.min(m.Log.id).label("evidence_id"), func.min(m.Log.timestamp).label("first_seen"), func.max(m.Log.timestamp).label("last_seen")), start, end, service).where(func.lower(m.Log.level).in_(ERROR_LEVELS)).group_by(pattern).order_by(func.count().desc(), pattern).limit(20))).mappings().all()
    patterns = [{"fingerprint": hashlib.sha256(row["pattern"].encode()).hexdigest(), "pattern": redact(row["pattern"])[:600], "count": row["count"], "evidence_id": row["evidence_id"], "first_seen": row["first_seen"].isoformat()+"Z", "last_seen": row["last_seen"].isoformat()+"Z"} for row in top]
    services = (await session.execute(scope(select(m.Log.service, func.count().label("logs"), func.count().filter(func.lower(m.Log.level).in_(ERROR_LEVELS)).label("errors")), start, end, service).group_by(m.Log.service).order_by(func.count().desc(), m.Log.service).limit(30))).mappings().all()
    watermark = (await session.execute(scope(select(func.max(m.Log.id)), start, end, service))).scalar() or 0
    first_last = (await session.execute(scope(select(func.min(m.Log.timestamp), func.max(m.Log.timestamp)), start, end, service))).one()
    total_services = (await session.execute(scope(select(func.count(func.distinct(m.Log.service))), start, end, service))).scalar()
    hourly = func.date_trunc("hour", m.Log.timestamp)
    timeline = (await session.execute(scope(select(hourly.label("time"), func.count().label("logs"), func.count().filter(func.lower(m.Log.level).in_(ERROR_LEVELS)).label("errors")), start, end, service).group_by(hourly).order_by(hourly))).mappings().all()
    return {
        "version": VERSION, "service": service, "window_start": start.isoformat()+"Z", "window_end": end.isoformat()+"Z",
        "totals": current, "comparison": {"logs": delta(current["total"], previous["total"], comparable), "errors": delta(current["errors"], previous["errors"], comparable)},
        "patterns": patterns, "services": [dict(row) for row in services], "affected_services": total_services,
        "timeline": [{"timestamp": row["time"].isoformat()+"Z", "logs": row["logs"], "errors": row["errors"]} for row in timeline],
        "source_max_id": watermark, "source_count": current["total"],
        "data_first_seen": first_last[0].isoformat()+"Z" if first_last[0] else None,
        "data_last_seen": first_last[1].isoformat()+"Z" if first_last[1] else None,
        "explanation": f"{current['total']:,} log events from {total_services} service(s), including {current['errors']:,} errors and {current['warnings']:,} warnings. " + ("No events arrived in this window." if not current["total"] else "Counts cover every retained event in the window. Top patterns are normalized and redacted; they do not establish a root cause."),
        "coverage": "Event presence does not prove continuous collection. Missing collector data cannot be interpreted as healthy operation.",
        "generated_at": datetime.utcnow().isoformat()+"Z",
    }


async def retention_days(session):
    row = (await session.execute(select(m.Plan.limits).join(m.Subscription, m.Subscription.plan_id == m.Plan.id).where(m.Subscription.tenant_id == identity()))).scalar_one_or_none()
    return int((row or {}).get("retention_days", 3))


def history_record(row):
    resolution = row.resolution or {}
    return {"id": row.id, "service": row.service, "summary": redact(row.summary), "suggested_cause": redact(row.root_cause), "reason": redact(row.reason), "suggestion": redact(row.suggestion), "severity": row.severity, "status": row.status, "feedback": redact(row.feedback), "resolution": {k: redact(v) if isinstance(v, str) else v for k, v in resolution.items()}, "created_at": row.created_at.isoformat()+"Z", "resolved_at": row.resolved_at.isoformat()+"Z" if row.resolved_at else None}
