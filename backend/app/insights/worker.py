"""Incremental hourly/daily summaries, independent from LLM analysis."""
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func, delete, text
from sqlalchemy.dialects.postgresql import insert
from app.db import models as m
from app.db.session import engine, session_scope
from app.tenancy import tenant_scope
from app.insights.history import digest, counts, scope, VERSION
from app.insights.recurrence import refresh as refresh_recurrence

log = logging.getLogger(__name__)


async def save_window(session, start, end, service, retention_start):
    if start < retention_start:
        return
    row = (await session.execute(select(m.LogSummary).where(m.LogSummary.service == service, m.LogSummary.window_start == start, m.LogSummary.window_end == end))).scalar_one_or_none()
    source_count, source_max = (await session.execute(scope(select(func.count(), func.max(m.Log.id)), start, end, service))).one()
    previous = await counts(session, start-(end-start), start, service) if start-(end-start) >= retention_start else {"total": None, "errors": None}
    if row and row.source_count == source_count and row.source_max_id == (source_max or 0) and row.content.get("version") == VERSION and row.content.get("comparison", {}).get("logs", {}).get("previous") == previous["total"] and row.content.get("comparison", {}).get("errors", {}).get("previous") == previous["errors"]:
        return
    if not source_count and not row:
        return
    result = await digest(session, start, end, service, retention_start)
    from app.tenancy import identity
    payload = {"tenant_id": identity(), "service": service, "window_start": start, "window_end": end, "source_max_id": result["source_max_id"], "source_count": result["source_count"], "content": result, "updated_at": datetime.utcnow()}
    statement = insert(m.LogSummary).values(**payload)
    await session.execute(statement.on_conflict_do_update(constraint="uq_log_summary_window", set_={k: statement.excluded[k] for k in ("source_max_id", "source_count", "content", "updated_at")}))


async def cycle():
    async with engine.connect() as connection:
        locked = (await connection.execute(text("SELECT pg_try_advisory_lock(7391047)"))).scalar()
        if not locked:
            return
        try:
            now = datetime.utcnow()
            hour = now.replace(minute=0, second=0, microsecond=0)
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            with tenant_scope(None):
                async with session_scope() as session:
                    customers = (await session.execute(select(m.Subscription.tenant_id, m.Plan.limits, m.Organization.status).join(m.Plan, m.Plan.id == m.Subscription.plan_id).join(m.Organization, m.Organization.id == m.Subscription.tenant_id).where(m.Subscription.tenant_id != 1))).all()
            for tenant, limits, status in customers:
                try:
                    cutoff = now-timedelta(days=int(limits.get("retention_days", 3)))
                    with tenant_scope(tenant):
                        async with session_scope() as session:
                            await session.execute(delete(m.LogSummary).where(m.LogSummary.window_start < cutoff))
                            if status != "active":
                                continue
                            await session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                            await refresh_recurrence(session, now, cutoff)
                            services = (await session.execute(select(m.Service.name).where(m.Service.active.is_(True)).order_by(m.Service.id).limit(1000))).scalars().all()
                        # All-services digests and one digest per registered service.
                        for service in ["", *services]:
                            for start, end in ((hour-timedelta(hours=1), hour), (hour-timedelta(hours=2), hour-timedelta(hours=1)), (day-timedelta(days=1), day)):
                                async with session_scope() as session:
                                    await session.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                                    await save_window(session, start, end, service, cutoff)
                            await asyncio.sleep(0)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Avoid logging query parameters or customer message content.
                    log.warning("Historical summaries could not be updated for workspace %s; retry next cycle", tenant)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(7391047)"))


async def run():
    while True:
        try:
            await cycle()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Historical summary worker is unavailable; retry next cycle")
        await asyncio.sleep(300)
