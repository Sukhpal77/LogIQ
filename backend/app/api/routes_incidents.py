"""Incident listing + feedback (closes the learning loop)."""
from datetime import datetime
import asyncio
import json
from app.ai.embeddings import embed
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import current_user, require_roles
from app.db import models
from app.db.session import get_session
from app.models.schemas import IncidentFeedback, IncidentOut

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=List[IncidentOut])
async def list_incidents(
    status: Optional[str] = None,
    service: Optional[str] = None,
    limit: int = Query(50, le=500),
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(current_user),
) -> List[IncidentOut]:
    q = select(models.Incident).order_by(desc(models.Incident.created_at)).limit(limit)
    if status:
        q = q.where(models.Incident.status == status)
    if service:
        q = q.where(models.Incident.service == service)
    rows = (await session.execute(q)).scalars().all()
    return [IncidentOut.model_validate(r) for r in rows]


@router.get("/incidents/{incident_id}", response_model=IncidentOut)
async def get_incident(
    incident_id: int,
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(current_user),
) -> IncidentOut:
    row = (
        await session.execute(select(models.Incident).where(models.Incident.id == incident_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "incident not found")
    return IncidentOut.model_validate(row)


@router.post("/incidents/{incident_id}/feedback", response_model=IncidentOut)
async def submit_feedback(
    incident_id: int,
    body: IncidentFeedback,
    session: AsyncSession = Depends(get_session, scope="function"),
    user=Depends(require_roles("admin", "devops")),
) -> IncidentOut:
    row = (
        await session.execute(select(models.Incident).where(models.Incident.id == incident_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "incident not found")
    row.feedback = body.feedback
    memory = (await session.execute(select(models.IncidentMemory).where(
        models.IncidentMemory.incident_id == row.id))).scalars().all()
    corrected = json.dumps({"service": row.service, "summary": row.summary,
                            "root_cause": row.root_cause, "feedback": body.feedback})
    from app.ai.metering import allow_provider
    token = allow_provider.set(False)
    try:
        vector = await asyncio.to_thread(embed, corrected)
    finally:
        allow_provider.reset(token)
    for entry in memory:
        entry.text, entry.embedding = corrected, vector
        entry.embedding_version = getattr(vector, "version", "legacy-unknown")
    if body.status:
        row.status = body.status
        if body.status in ("resolved", "dismissed"):
            row.resolved_at = datetime.utcnow()
        else:
            row.resolved_at = None
    await session.flush()
    return IncidentOut.model_validate(row)
