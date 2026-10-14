"""Service dependency graph CRUD + queries."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from app.tenancy import identity
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import current_user, require_roles
from app.context import graph
from app.db import models
from app.db.session import get_session
from app.models.schemas import DependencyEdge, ServiceNode

router = APIRouter(prefix="/context", tags=["context"])


@router.post("/services", status_code=201)
async def add_service(
    node: ServiceNode,
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(require_roles("admin", "devops")),
) -> dict:
    if identity() != 1:
        from app.api.routes_workspace import entitlement
        await session.execute(select(models.Organization).where(models.Organization.id == identity()).with_for_update())
        app_id = (node.metadata or {}).get("application_id")
        app = (await session.execute(select(models.Application).where(models.Application.id == app_id, models.Application.archived.is_(False)))).scalar_one_or_none()
        if app is None or not node.name.startswith(f"{app.name}/{app.environment}/"):
            raise HTTPException(422, "register a qualified service belonging to one of your applications")
        existing = (await session.execute(select(models.Service).where(models.Service.name == node.name))).scalar_one_or_none()
        if existing is None or not existing.active:
            used = (await session.execute(select(func.count()).select_from(models.Service).where(models.Service.active.is_(True)))).scalar()
            await entitlement(session, "services", used)
        if existing:
            existing.active = True
    svc = await graph.upsert_service(session, node)
    return {"id": svc.id, "name": svc.name}


@router.post("/dependencies", status_code=201)
async def add_dep(
    edge: DependencyEdge,
    session: AsyncSession = Depends(get_session, scope="function"),
    _user=Depends(require_roles("admin", "devops")),
) -> dict:
    if identity() != 1:
        registered = (await session.execute(select(models.Service.name).where(models.Service.name.in_([edge.source, edge.target]), models.Service.active.is_(True)))).scalars().all()
        if set(registered) != {edge.source, edge.target}:
            raise HTTPException(422, "register both services before adding a dependency")
    dep = await graph.add_dependency(session, edge)
    return {"id": dep.id}


@router.get("/services", response_model=List[ServiceNode])
async def list_services(
    session: AsyncSession = Depends(get_session, scope="function"), _user=Depends(current_user)
) -> List[ServiceNode]:
    rows = (await session.execute(select(models.Service).where(models.Service.active.is_(True)))).scalars().all()
    return [ServiceNode(name=r.name, kind=r.kind, metadata=r.metadata_json) for r in rows]


@router.get("/services/{name}/neighborhood")
async def neighborhood(
    name: str, session: AsyncSession = Depends(get_session, scope="function"), _user=Depends(current_user)
) -> dict:
    return await graph.neighborhood(session, name)
