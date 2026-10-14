"""Service dependency graph stored in Postgres.

Nodes are services, edges are typed relations (calls/reads/writes). The
context engine answers "what depends on X?" / "what does X depend on?" so
the AI agents can reason about blast radius and likely upstream causes.
"""
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db import models
from app.models.schemas import DependencyEdge, ServiceNode


async def upsert_service(session: AsyncSession, node: ServiceNode) -> models.Service:
    existing = (
        await session.execute(select(models.Service).where(models.Service.name == node.name))
    ).scalar_one_or_none()
    if existing:
        existing.kind = node.kind
        existing.metadata_json = node.metadata
        return existing
    svc = models.Service(name=node.name, kind=node.kind, metadata_json=node.metadata)
    session.add(svc)
    await session.flush()
    return svc


async def add_dependency(session: AsyncSession, edge: DependencyEdge) -> models.Dependency:
    src = (await session.execute(select(models.Service).where(models.Service.name == edge.source))).scalar_one_or_none()
    tgt = (await session.execute(select(models.Service).where(models.Service.name == edge.target))).scalar_one_or_none()
    src = src or await upsert_service(session, ServiceNode(name=edge.source))
    tgt = tgt or await upsert_service(session, ServiceNode(name=edge.target))
    existing = (await session.execute(select(models.Dependency).where(
        models.Dependency.source_id == src.id, models.Dependency.target_id == tgt.id,
        models.Dependency.relation == edge.relation).limit(1))).scalar_one_or_none()
    if existing:
        return existing
    dep = models.Dependency(source_id=src.id, target_id=tgt.id, relation=edge.relation)
    session.add(dep)
    await session.flush()
    return dep


async def upstream_of(session: AsyncSession, name: str) -> List[str]:
    """Return services that the given service calls/reads/writes (its dependencies)."""
    source = aliased(models.Service)
    res = await session.execute(
        select(models.Service.name)
        .join(models.Dependency, models.Dependency.target_id == models.Service.id)
        .join(source, models.Dependency.source_id == source.id)
        .where(source.name == name)
    )
    return [row[0] for row in res.all()]


async def downstream_of(session: AsyncSession, name: str) -> List[str]:
    """Return services that depend on the given service."""
    src = (
        await session.execute(select(models.Service).where(models.Service.name == name))
    ).scalar_one_or_none()
    if not src:
        return []
    res = await session.execute(
        select(models.Service.name)
        .join(models.Dependency, models.Dependency.source_id == models.Service.id)
        .where(models.Dependency.target_id == src.id)
    )
    return [row[0] for row in res.all()]


async def neighborhood(session: AsyncSession, name: str) -> Dict[str, List[str]]:
    return {
        "upstream": await upstream_of(session, name),
        "downstream": await downstream_of(session, name),
    }
