"""Normalize incoming logs from heterogeneous sources (Fluent Bit, Node, Nginx).

Fluent Bit forwards records as JSON; we coerce them into a LogEntry shape.
"""
from datetime import datetime, timezone
from typing import Any, Dict

from app.models.schemas import LogEntry, MetricEntry


_LEVEL_ALIASES = {
    "warning": "warn",
    "err": "error",
    "fatal": "error",
    "trace": "debug",
}


def _level(raw: Any) -> str:
    s = str(raw or "info").lower()
    return _LEVEL_ALIASES.get(s, s)


def _ts(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc).replace(tzinfo=None) if raw.tzinfo else raw
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=timezone.utc).replace(tzinfo=None)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return datetime.utcnow()


def normalize_log(record: Dict[str, Any]) -> LogEntry:
    """Accepts a raw Fluent Bit / app record and returns a LogEntry."""
    service = record.get("service") or record.get("kubernetes_container_name") or record.get("tag") or "unknown"
    level = _level(record.get("level") or record.get("severity") or record.get("log_level"))
    message = record.get("message") or record.get("log") or record.get("msg") or ""
    ts = _ts(record.get("timestamp") or record.get("time") or record.get("@timestamp"))

    known = {"service", "level", "message", "timestamp", "log", "msg", "severity", "log_level", "time", "@timestamp", "attrs", "event_id"}
    attrs = {k: v for k, v in record.items() if k not in known}
    attrs.update(record.get("attrs") or {})
    return LogEntry(
        event_id=record.get("event_id"),
        service=str(service),
        level=level,
        message=str(message).strip(),
        timestamp=ts,
        attrs=attrs or None,
    )


def normalize_metric(record: Dict[str, Any]) -> MetricEntry:
    return MetricEntry(
        event_id=record.get("event_id"),
        service=str(record.get("service") or "default"),
        cpu=float(record.get("cpu", 0.0)),
        memory=float(record.get("memory", 0.0)),
        latency=float(record.get("latency", 0.0)),
        timestamp=_ts(record.get("timestamp")),
    )
