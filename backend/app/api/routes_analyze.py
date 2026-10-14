"""Run end-to-end analysis: anomaly detection -> AI agents -> action recommendations.

Persists a new Incident row whenever an anomaly is found, and writes an
embedding into incident_memory so future analyses can use RAG.
"""
import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import agents
from app.ai.embeddings import embed
from app.anomaly.detector import run as run_detection
from app.auth.rbac import current_user, require_roles
from app.context.graph import neighborhood
from app.db import models
from app.db.session import get_session
from app.models.schemas import AnalysisResponse, AnomalyFinding, LogEntry, MetricEntry
from app.config import settings
from app.tenancy import identity
from app.ai.metering import meter, allow_provider
import uuid

router = APIRouter(tags=["analyze"])


async def _recent_metrics(session: AsyncSession, service: Optional[str], n: int = 200) -> List[MetricEntry]:
    q = select(models.Metric).where(models.Metric.timestamp >= datetime.utcnow() - timedelta(
        minutes=settings.analysis_window_minutes)).order_by(desc(models.Metric.timestamp)).limit(n)
    if service:
        q = q.where(models.Metric.service == service)
        closed = select(func.max(models.Incident.resolved_at)).where(models.Incident.service == service).scalar_subquery()
        q = q.where(models.Metric.timestamp > func.coalesce(closed, datetime(1970, 1, 1)))
    rows = (await session.execute(q)).scalars().all()
    rows = list(reversed(rows))
    return [
        MetricEntry(service=r.service, cpu=r.cpu, memory=r.memory, latency=r.latency, timestamp=r.timestamp)
        for r in rows
    ]


async def _recent_error_logs(session: AsyncSession, service: Optional[str], n: int = 10) -> List[LogEntry]:
    q = (
        select(models.Log)
        .where(models.Log.level.in_(["error", "fatal", "critical"]))
        .where(models.Log.timestamp >= datetime.utcnow() - timedelta(minutes=settings.analysis_window_minutes))
        .order_by(desc(models.Log.timestamp))
        .limit(n)
    )
    if service:
        q = q.where(models.Log.service == service)
        closed = select(func.max(models.Incident.resolved_at)).where(models.Incident.service == service).scalar_subquery()
        q = q.where(models.Log.timestamp > func.coalesce(closed, datetime(1970, 1, 1)))
    rows = (await session.execute(q)).scalars().all()
    rows = list(reversed(rows))
    return [LogEntry(service=r.service, level=r.level, message=r.message, timestamp=r.timestamp) for r in rows]


async def _recall_similar(session: AsyncSession, query_embedding, service="", query_text="") -> List[str]:
    from app.insights.history import redact
    version = getattr(query_embedding, "version", "legacy-unknown")
    if not version.startswith("openai:"):
        # Hash vectors have no semantic similarity. Use transparent keyword
        # matches to confirmed incidents from the same service instead.
        words = {word for word in ("timeout", "deadlock", "connection", "memory", "cpu", "latency", "refused", "unavailable", "disk", "pool") if word in query_text.lower()}
        if not words or not service:
            return []
        rows = (await session.execute(select(models.Incident).where(models.Incident.service == service, models.Incident.resolution["confirmed"].as_boolean().is_(True), models.Incident.status == "resolved").order_by(models.Incident.created_at.desc()).limit(50))).scalars().all()
        ranked = sorted(((len(words & {word for word in words if word in (row.summary+" "+str(row.resolution)).lower()}), row) for row in rows), key=lambda pair: pair[0], reverse=True)
        return [json.dumps({"incident_id": row.id, "match": "keyword match; not a verified current cause", "summary": redact(row.summary), "confirmed_resolution": {k: redact(v) if isinstance(v, str) else v for k, v in (row.resolution or {}).items()}}) for score, row in ranked[:3] if score]
    query = select(models.IncidentMemory.text, models.Incident.resolution, models.Incident.id).join(models.Incident, models.Incident.id == models.IncidentMemory.incident_id).where(models.IncidentMemory.embedding_version == version)
    if service:
        query = query.where(models.Incident.service == service)
    rows = (await session.execute(query.order_by(models.IncidentMemory.embedding.l2_distance(query_embedding)).limit(3))).all()
    return [json.dumps({"incident_id": identifier, "match": "semantic similarity; not a verified current cause", "previous_summary": redact(message), "confirmed_resolution": {k: redact(v) if isinstance(v, str) else v for k, v in (resolution or {}).items()}}) for message, resolution, identifier in rows]


@router.get("/analyze", response_model=AnalysisResponse)
@router.post("/analyze", response_model=AnalysisResponse)
async def analyze(
    service: Optional[str] = None,
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(require_roles("admin", "devops")),
    request: Request = None,
) -> AnalysisResponse:
    if request and request.method == "GET" and request.cookies.get("logiq_session"):
        raise HTTPException(405, "use POST for analysis from a browser session")
    if not service:
        raise HTTPException(400, "service is required; analyze each service separately")
    from app.api.routes_workspace import entitlement, subscription
    allowed = True
    if identity() != 1:
        await session.execute(select(models.Organization).where(models.Organization.id == identity()).with_for_update())
        sub, plan = await subscription(session)
        credits = (await session.execute(select(func.coalesce(func.sum(models.Usage.amount), 0)).where(models.Usage.kind == "ai_credits", models.Usage.created_at >= sub.period_start))).scalar()
        try:
            await entitlement(session, "ai_credits", credits)
        except HTTPException:
            allowed = False  # Deterministic monitoring continues when the AI budget ends.
    statistics = {"calls": 0, "successes": 0, "input_tokens": 0, "output_tokens": 0, "embedding_tokens": 0}
    meter.set(statistics)
    allow_provider.set(allowed)
    # Serializes incident creation across replicas and concurrent manual requests.
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:service))"), {"service": f"{identity()}:{service}"})
    metrics = await _recent_metrics(session, service)
    error_logs = await _recent_error_logs(session, service)
    is_anomaly, findings, severity = run_detection(metrics)
    if error_logs and severity == "low":
        severity = "medium"

    deps: List[str] = []
    if service:
        nb = await neighborhood(session, service)
        deps = nb["upstream"] + nb["downstream"]

    summary_text = f"service={service or 'all'} severity={severity} flagged={[f.field for f in findings if f.flagged]}"
    similar = await _recall_similar(session, await asyncio.to_thread(embed, summary_text), service, summary_text) if (is_anomaly or error_logs) else []

    state = await asyncio.to_thread(agents.run,
        agents.BrainState(
            service=service or "all",
            findings=[f.model_dump() for f in findings],
            logs=[l.model_dump(mode="json") for l in error_logs],
            similar_incidents=similar,
            dependencies=deps,
            severity=severity,
        )
    )

    incident_id: Optional[int] = None
    new_incident = False
    if is_anomaly or error_logs:
        incident = (await session.execute(select(models.Incident).where(
            models.Incident.service == service, models.Incident.status == "open"
        ).order_by(desc(models.Incident.created_at)).limit(1))).scalar_one_or_none()
        new_incident = incident is None
        values = dict(
            service=service or "all",
            summary=state.get("analysis", "")[:240],
            reason=state.get("analysis", ""),
            root_cause=state.get("root_cause", ""),
            suggestion=state.get("suggestion", ""),
            severity=severity,
            findings={"items": [f.model_dump() for f in findings]},
        )
        if incident is None:
            incident = models.Incident(**values)
            session.add(incident)
        else:
            for key, value in values.items():
                setattr(incident, key, value)
        await session.flush()
        incident_id = incident.id

        memory_text = json.dumps(
            {
                "service": incident.service,
                "severity": severity,
                "summary": incident.summary,
                "root_cause": incident.root_cause,
            }
        )
        if new_incident:
            memory_vector = await asyncio.to_thread(embed, memory_text)
            session.add(
                models.IncidentMemory(
                    incident_id=incident.id, text=memory_text, embedding=memory_vector, embedding_version=getattr(memory_vector, "version", "legacy-unknown")
                )
            )
            if (await session.execute(select(models.WorkspaceSetting.id).where(models.WorkspaceSetting.kind == "notification", models.WorkspaceSetting.enabled.is_(True)).limit(1))).first():
                session.add(models.NotificationOutbox(incident_id=incident.id,
                    text=f"[LOGIQ] #{incident.id} {service} ({severity}): {incident.summary}"))

    if statistics["successes"]:
        reference = uuid.uuid4().hex
        for kind in ("input_tokens", "output_tokens", "embedding_tokens"):
            if statistics[kind]:
                session.add(models.Usage(kind=kind, amount=statistics[kind], reference=f"{reference}:{kind}"))
        session.add(models.Usage(kind="ai_credits", amount=1, reference=f"{reference}:analysis"))
    await session.commit()
    allow_provider.set(True)
    meter.set(None)
    return AnalysisResponse(
        mode="provider-assisted" if statistics.get("reasoning_successes") else "heuristic",
        provider=settings.llm_provider if statistics.get("reasoning_successes") else "disabled/fallback",
        anomaly=is_anomaly or bool(error_logs),
        severity=severity,
        reason=state.get("analysis", ""),
        root_cause=state.get("root_cause", ""),
        suggestion=state.get("suggestion", ""),
        actions=state.get("actions", []),
        findings=[AnomalyFinding(**f) if isinstance(f, dict) else f for f in findings],
        recent_error_logs=error_logs,
        incident_id=incident_id,
    )
