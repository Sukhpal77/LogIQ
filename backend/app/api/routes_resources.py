from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import models as m
from app.db.session import get_session
from app.auth.rbac import require_roles
from app.api.routes_workspace import audit
from app.tenancy import identity
from app.api.routes_workspace import entitlement
from sqlalchemy import func
router=APIRouter(tags=["resources"])


@router.delete("/workspace/applications/{application_id}")
async def archive(application_id:int,session:AsyncSession=Depends(get_session, scope="function"),user=Depends(require_roles("admin","devops"))):
    app=(await session.execute(select(m.Application).where(m.Application.id==application_id).with_for_update())).scalar_one_or_none()
    if app is None:
        raise HTTPException(404,"application not found")
    app.archived=True
    for key in (await session.execute(select(m.ApiKey).where(m.ApiKey.tenant_id==identity(),m.ApiKey.application_id==app.id))).scalars():
        key.active=False
    for service in (await session.execute(select(m.Service))).scalars():
        if (service.metadata_json or {}).get("application_id")==app.id:
            service.active=False
    audit(session,user,"application.archive",{"application_id":app.id})
    return {"status":"archived; historical logs and metrics retained"}


@router.post("/workspace/applications/{application_id}/restore")
async def restore(application_id:int,session:AsyncSession=Depends(get_session, scope="function"),user=Depends(require_roles("admin","devops"))):
    await session.execute(select(m.Organization).where(m.Organization.id==identity()).with_for_update())
    app=(await session.execute(select(m.Application).where(m.Application.id==application_id).with_for_update())).scalar_one_or_none()
    if app is None:
        raise HTTPException(404,"application not found")
    if not app.archived:
        return {"status":"application already active"}
    used=(await session.execute(select(func.count()).select_from(m.Application).where(m.Application.archived.is_(False)))).scalar()
    plan=await entitlement(session,"applications",used)
    services=(await session.execute(select(m.Service))).scalars().all()
    associated=[s for s in services if (s.metadata_json or {}).get("application_id")==app.id and not s.active]
    if sum(s.active for s in services)+len(associated)>plan.limits["services"]:
        raise HTTPException(429,"restoring these services exceeds the purchased service limit")
    for service in associated:
        service.active=True
    app.archived=False
    audit(session,user,"application.restore",{"application_id":app.id})
    return {"status":"restored; create new keys to resume collection"}
