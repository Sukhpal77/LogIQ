"""Admission control and application namespaces for customer ingestion."""
from sqlalchemy import select, func
from fastapi import HTTPException
from app.db import models as m
from app.db.session import session_scope
from app.api.routes_workspace import entitlement
from app.tenancy import identity


async def prepare(records, user):
    from app.ingestion.redaction import redact
    for record in records:
        if "message" in record:
            record["message"] = redact(record["message"])
        if record.get("attrs"):
            record["attrs"] = redact(record["attrs"])
    if identity() == 1 and user.application_id is None:
        return records  # Legacy integration is scoped to workspace 1 during migration.
    async with session_scope() as s:
        await s.execute(select(m.Organization).where(m.Organization.id == identity()).with_for_update())
        plan = await entitlement(s, "ingestion_gb")
        app = (await s.execute(select(m.Application).where(m.Application.id == user.application_id))).scalar_one_or_none() if user.application_id else None
        if user.application_id and app is None:
            raise HTTPException(403, "application unavailable")
        for record in records:
            if app:
                record["service"] = f"{app.name}/{app.environment}/{record['service']}"
            if len(record["service"]) > 128:
                raise HTTPException(422, "qualified service name exceeds 128 characters")
            existing = (await s.execute(select(m.Service).where(m.Service.name == record["service"]))).scalar_one_or_none()
            if existing is None:
                used = (await s.execute(select(func.count()).select_from(m.Service).where(m.Service.active.is_(True)))).scalar()
                await entitlement(s, "services", used)
                s.add(m.Service(name=record["service"], kind="app", metadata_json={"application_id": app.id if app else None}))
                await s.flush()
            elif not existing.active:
                raise HTTPException(403, "service archived; reactivate it before collection")
    return records
