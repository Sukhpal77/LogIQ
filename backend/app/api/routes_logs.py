"""Log ingestion + retrieval routes."""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import current_user, ingest_user
from app.db import models
from app.db.session import get_session
from app.ingestion.stream import publish_log
from app.models.schemas import LogEntry
from app.ingestion.normalizer import normalize_log
from fastapi import Body, HTTPException
from app.ingestion.limits import prepare

router = APIRouter(tags=["logs"])


@router.post("/ingest/logs", status_code=202)
async def ingest_raw_logs(payload: List[dict] | dict = Body(...), _user=Depends(ingest_user)):
    records = payload if isinstance(payload, list) else [payload]
    if not 1 <= len(records) <= 100:
        raise HTTPException(413, "send between 1 and 100 records")
    try:
        normalized = [normalize_log(record) for record in records]
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "invalid collector record") from error
    records = await prepare([entry.model_dump(mode="json") for entry in normalized], _user)
    for record in records:
        await publish_log(record)
    return {"status": "queued", "count": len(normalized)}


@router.post("/logs", status_code=202)
async def ingest_log(entry: LogEntry, _user=Depends(ingest_user)) -> dict:
    """Publish to Redis Stream — the worker persists asynchronously."""
    record = (await prepare([entry.model_dump(mode="json")], _user))[0]
    record_id = await publish_log(record)
    return {"status": "queued", "id": record_id}


@router.post("/logs/bulk", status_code=202)
async def ingest_logs_bulk(entries: List[LogEntry], _user=Depends(ingest_user)) -> dict:
    if not 1 <= len(entries) <= 100:
        from fastapi import HTTPException
        raise HTTPException(413, "send between 1 and 100 records")
    ids = []
    for record in await prepare([e.model_dump(mode="json") for e in entries], _user):
        ids.append(await publish_log(record))
    return {"status": "queued", "count": len(ids)}


@router.get("/logs", response_model=List[LogEntry])
async def list_logs(
    service: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = Query(100, le=1000),
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(current_user),
) -> List[LogEntry]:
    q = select(models.Log).order_by(desc(models.Log.timestamp)).limit(limit)
    if service:
        q = q.where(models.Log.service == service)
    if level:
        q = q.where(models.Log.level == level)
    rows = (await session.execute(q)).scalars().all()
    return [
        LogEntry(
            service=r.service, level=r.level, message=r.message, timestamp=r.timestamp, attrs=r.attrs
        )
        for r in rows
    ]
