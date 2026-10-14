"""Metrics ingestion + retrieval routes."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import current_user, ingest_user
from app.db import models
from app.db.session import get_session
from app.ingestion.stream import publish_metric
from app.models.schemas import MetricEntry
from app.ingestion.limits import prepare

router = APIRouter(tags=["metrics"])


@router.post("/metrics", status_code=202)
async def ingest_metric(entry: MetricEntry, _user=Depends(ingest_user)) -> dict:
    record_id = await publish_metric((await prepare([entry.model_dump(mode="json")], _user))[0])
    return {"status": "queued", "id": record_id}


@router.post("/metrics/bulk", status_code=202)
async def ingest_metrics_bulk(entries: List[MetricEntry], _user=Depends(ingest_user)) -> dict:
    if not 1 <= len(entries) <= 100:
        from fastapi import HTTPException
        raise HTTPException(413, "send between 1 and 100 records")
    for record in await prepare([e.model_dump(mode="json") for e in entries], _user):
        await publish_metric(record)
    return {"status": "queued", "count": len(entries)}


@router.get("/metrics", response_model=List[MetricEntry])
async def list_metrics(
    service: Optional[str] = None,
    limit: int = Query(100, le=1000),
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(current_user),
) -> List[MetricEntry]:
    q = select(models.Metric).order_by(desc(models.Metric.timestamp)).limit(limit)
    if service:
        q = q.where(models.Metric.service == service)
    rows = (await session.execute(q)).scalars().all()
    rows = list(reversed(rows))
    return [
        MetricEntry(
            service=r.service, cpu=r.cpu, memory=r.memory, latency=r.latency, timestamp=r.timestamp
        )
        for r in rows
    ]
