"""Periodic per-service analysis and retention; one active cycle across replicas."""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import delete, select, text, func
from app.config import settings
from app.db import models
from app.db.session import engine, session_scope
from app.db.redis_client import get_redis
from app.tenancy import tenant_scope, identity

log = logging.getLogger(__name__)


async def notifications():
    for _ in range(100):
        async with session_scope() as session:
            item = (await session.execute(select(models.NotificationOutbox).where(
                models.NotificationOutbox.sent_at.is_(None),
                models.NotificationOutbox.attempts < 20,
                models.NotificationOutbox.next_attempt <= datetime.utcnow()
            ).order_by(models.NotificationOutbox.id).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
            if item is None:
                return
            from app.actions.delivery import deliver
            with tenant_scope(item.tenant_id):
                destinations = (await session.execute(select(models.WorkspaceSetting).where(models.WorkspaceSetting.kind == "notification", models.WorkspaceSetting.enabled.is_(True)))).scalars().all()
                from app.api.routes_notification_config import matches
                incident = await session.get(models.Incident, item.incident_id) if item.incident_id else None
                destinations = [d for d in destinations if incident and matches(d.config, incident)]
                if destinations:
                    outcomes = [await deliver(destination.config, item.text) for destination in destinations]
                    from app.models.schemas import ActionResult
                    result = ActionResult(kind="notify", target="customer destinations", success=all(r["success"] for r in outcomes), detail=str(outcomes))
                else:
                    item.sent_at = datetime.utcnow()
                    continue
            item.attempts += 1
            if result.success:
                item.sent_at = datetime.utcnow()
                incident = await session.get(models.Incident, item.incident_id, with_for_update=True) if item.incident_id is not None else None
                if incident:
                    incident.actions_taken = {"items": [*(incident.actions_taken or {}).get("items", []), result.model_dump()]}
            else:
                item.next_attempt = datetime.utcnow() + timedelta(seconds=min(300, 2 ** min(item.attempts, 9)))
                log.warning("Incident %s notification failed; retry scheduled", item.incident_id)


async def cycle():
    from app.api.routes_analyze import analyze
    # Session advisory lock stays held on this dedicated connection through the cycle.
    async with engine.connect() as connection:
        acquired = (await connection.execute(text("SELECT pg_try_advisory_lock(7391042)"))).scalar()
        if not acquired:
            return
        try:
            from app.api.routes_workspace import load_llm_settings
            await load_llm_settings()
            cutoff = datetime.utcnow() - timedelta(minutes=settings.analysis_window_minutes)
            with tenant_scope(None):
                async with session_scope() as session:
                    services = set((await session.execute(select(models.Metric.tenant_id, models.Metric.service).where(
                        models.Metric.timestamp >= cutoff).distinct())).all())
                    services.update((await session.execute(select(models.Log.tenant_id, models.Log.service).where(
                        models.Log.timestamp >= cutoff).distinct())).all())
            for tenant, service in (sorted(services) if settings.analysis_interval_seconds > 0 else []):
                try:
                    with tenant_scope(tenant):
                      async with session_scope() as session:
                        metric_id = (await session.execute(select(func.max(models.Metric.id)).where(models.Metric.service == service))).scalar() or 0
                        log_id = (await session.execute(select(func.max(models.Log.id)).where(models.Log.service == service))).scalar() or 0
                        marker = f"{metric_id}:{log_id}"
                        key = f"logiq:analysis:{tenant}:{service}"
                        if await get_redis().get(key) == marker:
                            continue
                        await analyze(service=service, session=session, _user=None)
                    await get_redis().set(key, marker, ex=86400)
                except Exception:
                    log.exception("Analysis failed for service %s", service)
            if settings.retention_days > 0:
                cutoff = datetime.utcnow() - timedelta(days=settings.retention_days)
                with tenant_scope(None):
                    async with session_scope() as session:
                        subscriptions = (await session.execute(select(models.Subscription))).scalars().all()
                    for sub in subscriptions:
                        with tenant_scope(sub.tenant_id):
                            async with session_scope() as session:
                                plan = await session.get(models.Plan, sub.plan_id)
                                cutoff = datetime.utcnow() - timedelta(days=plan.limits["retention_days"])
                                for table in (models.Log, models.Metric):
                                    await session.execute(delete(table).where(table.timestamp < cutoff))
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(7391042)"))


async def run():
    while True:
        try:
            await cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Monitoring cycle failed")
        await asyncio.sleep(max(30, settings.analysis_interval_seconds))


async def run_notifications():
    """Deliver alerts independently so long AI analyses do not hold up email."""
    from app.api.routes_notification_config import load_delivery_settings
    from app.actions.mail import drain
    while True:
        try:
            async with engine.connect() as connection:
                acquired = (await connection.execute(text("SELECT pg_try_advisory_lock(7391043)"))).scalar()
                if acquired:
                    try:
                        await load_delivery_settings()
                        with tenant_scope(None):
                            await notifications()
                            await drain()
                    finally:
                        await connection.execute(text("SELECT pg_advisory_unlock(7391043)"))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Notification delivery cycle failed")
        await asyncio.sleep(15)
