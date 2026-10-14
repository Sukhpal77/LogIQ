"""Personal dashboard layouts and tenant-scoped, bounded telemetry summaries."""
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select, func
from app.auth.rbac import current_user
from app.db import models as m
from app.db.session import get_session
from app.tenancy import identity

router = APIRouter(tags=["dashboards"])


class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    chart: Literal["line", "area", "bar", "scatter", "stat", "gauge", "donut", "table"] = "line"
    signal: Literal["cpu", "memory", "latency", "logs", "errors", "incidents"] = "cpu"
    service: str = Field(default="", max_length=128)
    aggregation: Literal["average", "maximum", "latest"] = "average"
    width: Literal[3, 4, 6, 8, 12] = 6
    height: int = Field(default=280, ge=200, le=600)
    color: str = Field(default="#21a98b", pattern=r"^#[0-9a-fA-F]{6}$")
    style: Literal["light", "dark", "minimal"] = "light"


class Dashboard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    minutes: int = Field(default=60, ge=1, le=43200)
    refresh: Literal[0, 15, 30, 60] = 30
    widgets: list[Widget] = Field(default_factory=list, max_length=24)

    @field_validator("widgets")
    @classmethod
    def unique_widgets(cls, widgets):
        if len({widget.id for widget in widgets}) != len(widgets):
            raise ValueError("widget IDs must be unique")
        return widgets


def owned(user):
    return select(m.WorkspaceSetting).where(m.WorkspaceSetting.tenant_id == identity(), m.WorkspaceSetting.kind == "dashboard", m.WorkspaceSetting.config["owner"].as_string() == user.username)


def serialize(row):
    return {"id": row.id, **row.config["layout"]}


@router.get("/workspace/dashboards")
async def dashboards(session=Depends(get_session, scope="function"), user=Depends(current_user)):
    return [serialize(row) for row in (await session.execute(owned(user).order_by(m.WorkspaceSetting.id))).scalars()]


@router.post("/workspace/dashboards", status_code=201)
async def create(body: Dashboard, session=Depends(get_session, scope="function"), user=Depends(current_user)):
    await session.execute(select(m.User).where(m.User.username == user.username, m.User.tenant_id == identity()).with_for_update())
    if len((await session.execute(owned(user))).scalars().all()) >= 10:
        raise HTTPException(429, "maximum 10 personal dashboards")
    row = m.WorkspaceSetting(kind="dashboard", name=body.name, config={"owner": user.username, "layout": body.model_dump()})
    session.add(row)
    await session.flush()
    return serialize(row)


@router.put("/workspace/dashboards/{dashboard_id}")
async def update(dashboard_id: int, body: Dashboard, session=Depends(get_session, scope="function"), user=Depends(current_user)):
    row = (await session.execute(owned(user).where(m.WorkspaceSetting.id == dashboard_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "dashboard not found")
    row.name = body.name
    row.config = {"owner": user.username, "layout": body.model_dump()}
    return serialize(row)


@router.delete("/workspace/dashboards/{dashboard_id}")
async def remove(dashboard_id: int, session=Depends(get_session, scope="function"), user=Depends(current_user)):
    row = (await session.execute(owned(user).where(m.WorkspaceSetting.id == dashboard_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "dashboard not found")
    await session.delete(row)
    return {"status": "deleted"}


@router.get("/workspace/dashboard-data")
async def data(service: str = Query("", max_length=128), minutes: int = Query(60, ge=1, le=43200), session=Depends(get_session, scope="function"), user=Depends(current_user)):
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    bucket = max(60, (minutes * 60 + 299) // 300)
    def scope(stmt, model, time):
        stmt = stmt.where(model.tenant_id == identity(), time >= cutoff)
        return stmt.where(model.service == service) if service else stmt
    def slot(time):
        return func.to_timestamp(func.floor(func.extract("epoch", time) / bucket) * bucket)
    metric_slot = slot(m.Metric.timestamp)
    metrics = (await session.execute(scope(select(metric_slot.label("time"), *[f(getattr(m.Metric, k)).label(k + suffix) for k in ("cpu", "memory", "latency") for f, suffix in ((func.avg, ""), (func.max, "_max"))]), m.Metric, m.Metric.timestamp).group_by(metric_slot).order_by(metric_slot))).mappings().all()
    log_slot = slot(m.Log.timestamp)
    logs = (await session.execute(scope(select(log_slot.label("time"), func.count().label("logs"), func.count().filter(func.upper(m.Log.level).in_(["ERROR", "CRITICAL", "FATAL"])).label("errors")), m.Log, m.Log.timestamp).group_by(log_slot).order_by(log_slot))).mappings().all()
    incident_slot = slot(m.Incident.created_at)
    incidents = (await session.execute(scope(select(incident_slot.label("time"), func.count().label("incidents")), m.Incident, m.Incident.created_at).group_by(incident_slot).order_by(incident_slot))).mappings().all()
    latest = (await session.execute(scope(select(m.Metric.cpu, m.Metric.memory, m.Metric.latency), m.Metric, m.Metric.timestamp).order_by(m.Metric.timestamp.desc(), m.Metric.id.desc()).limit(1))).mappings().first()
    averages = (await session.execute(scope(select(*[func.avg(getattr(m.Metric, k)).label(k) for k in ("cpu", "memory", "latency")]), m.Metric, m.Metric.timestamp))).mappings().one()
    from sqlalchemy import case
    levels = (await session.execute(scope(select(m.Log.level, func.count()), m.Log, m.Log.timestamp).group_by(m.Log.level))).all()
    severities = (await session.execute(scope(select(m.Incident.severity, func.count()), m.Incident, m.Incident.created_at).group_by(m.Incident.severity))).all()
    band = case((m.Metric.latency <= 100, "≤100 ms"), (m.Metric.latency <= 500, "100–500 ms"), else_=">500 ms")
    latency_bands = (await session.execute(scope(select(band, func.count()), m.Metric, m.Metric.timestamp).group_by(band))).all()
    series = {}
    for row in [*metrics, *logs, *incidents]:
        key = row["time"].isoformat()
        series.setdefault(key, {"timestamp": key}).update({k: float(v) if v is not None else None for k, v in row.items() if k != "time"})
    return {"items": [series[k] for k in sorted(series)], "bucket_seconds": bucket, "latest": dict(latest or {}), "averages": dict(averages), "distributions": {"logs": dict(levels), "errors": {level: count for level, count in levels if level.upper() in ("ERROR", "CRITICAL", "FATAL")}, "incidents": dict(severities), "latency": dict(latency_bands)}}
