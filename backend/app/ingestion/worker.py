"""Background consumer that persists streamed events to Postgres.

Runs as an asyncio task started during FastAPI lifespan. Reads from both
log and metric streams using XREADGROUP, persists each batch in a single
transaction, then ACKs.

Designed to survive restarts: pending entries are picked up via the
consumer group's pending list on next iteration.
"""
import asyncio
import json
import logging
import socket
import uuid
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.redis_client import get_redis
from app.db.session import session_scope
from app.db import models
from app.ingestion.normalizer import normalize_log, normalize_metric

log = logging.getLogger(__name__)
_CONSUMER = f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


async def _consume_once(stream: str, batch: int = 100) -> int:
    r = get_redis()
    reclaimed = await r.xautoclaim(stream, settings.consumer_group, _CONSUMER,
                                   min_idle_time=60000, start_id="0-0", count=batch)
    if reclaimed[1]:
        await _persist(stream, reclaimed[1])
    resp = await r.xreadgroup(
        groupname=settings.consumer_group,
        consumername=_CONSUMER,
        streams={stream: ">"},
        count=batch,
        block=2000,
    )
    if not resp:
        return 0

    persisted = 0
    for _, entries in resp:
        persisted += await _persist(stream, entries)
    return persisted


async def _persist(stream, entries):
    r = get_redis()
    ids = []
    async with session_scope() as session:
        for entry_id, fields in entries:
            try:
                payload = json.loads(fields["data"])
                e = normalize_log(payload) if stream == settings.log_stream else normalize_metric(payload)
                values = e.model_dump()
                values["tenant_id"] = int(payload.get("tenant_id", 1))
                values["event_id"] = str(e.event_id or uuid.uuid5(uuid.NAMESPACE_URL, f"logiq:{stream}:{entry_id}"))
            except (ValueError, TypeError, KeyError):
                await r.xadd(f"{stream}.dead", {"source_id": entry_id, "data": fields.get("data", "")})
                ids.append(entry_id)
                continue
            table = models.Log if stream == settings.log_stream else models.Metric
            result = await session.execute(insert(table).values(**values).on_conflict_do_nothing(index_elements=["tenant_id", "event_id"]).returning(table.event_id))
            if result is not None and result.scalar_one_or_none():
                await session.execute(insert(models.Usage).values(tenant_id=values["tenant_id"], kind="ingestion_bytes", amount=len(fields["data"].encode()), reference=f"{values['tenant_id']}:{stream}:{values['event_id']}").on_conflict_do_nothing(index_elements=["reference"]))
            ids.append(entry_id)
    if ids:
        await r.xack(stream, settings.consumer_group, *ids)
        await r.xdel(stream, *ids)
    return len(ids)


async def run() -> None:
    """Long-running loop. Cancellation-safe."""
    log.info("Ingestion worker starting (consumer=%s)", _CONSUMER)
    while True:
        try:
            await asyncio.gather(
                _consume_once(settings.log_stream),
                _consume_once(settings.metric_stream),
            )
        except asyncio.CancelledError:
            log.info("Ingestion worker stopping")
            raise
        except Exception:
            log.exception("Worker iteration failed")
            await asyncio.sleep(1.0)
