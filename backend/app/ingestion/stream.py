"""Redis Streams producer for logs and metrics.

We publish every accepted record to a stream so the consumer worker can
durably persist + analyze without blocking the API request.
"""
import json
from typing import Any, Dict

from app.config import settings
from app.db.redis_client import get_redis
from app.tenancy import identity
import uuid
from sqlalchemy import select, func
from app.db import models as m
from app.db.session import session_scope
from app.api.routes_workspace import subscription


async def publish(stream, record):
    r = get_redis()
    record = {**record, "tenant_id": identity()}
    record.setdefault("event_id", str(uuid.uuid4()))
    if not record["event_id"]:
        record["event_id"] = str(uuid.uuid4())
    data = json.dumps(record, default=str)
    if identity() == 1:
        return await r.xadd(stream, {"data": data})
    async with session_scope() as s:
        sub, plan = await subscription(s)
        ledger = (await s.execute(select(func.coalesce(func.sum(m.Usage.amount), 0)).where(m.Usage.kind == "ingestion_bytes", m.Usage.created_at >= sub.period_start))).scalar()
        duplicate_key = f"event:{identity()}:{stream}:{record['event_id']}"
        if not await r.exists(duplicate_key):
            table = m.Log if stream == settings.log_stream else m.Metric
            if (await s.execute(select(table.id).where(table.event_id == record["event_id"]).limit(1))).first():
                return "duplicate"
    period = sub.period_end.isoformat()
    # Redis AOF persists admission counters alongside the stream. UUID retries do not
    # consume quota twice; event keys expire after the billing/retry window.
    code = """
    if redis.call('EXISTS',KEYS[3]) == 1 then return 'duplicate' end
    local used = math.max(tonumber(redis.call('GET',KEYS[2]) or '0'), tonumber(ARGV[4]))
    if used + tonumber(ARGV[2]) > tonumber(ARGV[3]) then return 'limit' end
    local id = redis.call('XADD',KEYS[1],'*','data',ARGV[1])
    redis.call('SET',KEYS[2],used+tonumber(ARGV[2]),'EX',5184000)
    redis.call('SET',KEYS[3],id,'EX',5184000)
    return id
    """
    result = await r.eval(code, 3, stream, f"quota:{identity()}:{period}", duplicate_key, data, len(data.encode()), plan.limits["ingestion_gb"] * 1000000000, int(ledger))
    if result == "limit":
        from fastapi import HTTPException
        raise HTTPException(429, "ingestion quota reached; upgrade or wait for renewal")
    return result


async def publish_log(record: Dict[str, Any]) -> str:
    return await publish(settings.log_stream, record)


async def publish_metric(record: Dict[str, Any]) -> str:
    return await publish(settings.metric_stream, record)


async def ensure_groups() -> None:
    """Create consumer groups if they don't exist (idempotent)."""
    r = get_redis()
    for stream in (settings.log_stream, settings.metric_stream):
        try:
            await r.xgroup_create(stream, settings.consumer_group, id="0", mkstream=True)
        except Exception as e:  # group already exists
            if "BUSYGROUP" not in str(e):
                raise
