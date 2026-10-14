"""Durable localhost collector, using only the Python standard library."""
import argparse
import json
import logging
import math
import os
import random
import signal
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit

log = logging.getLogger("collector")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        # Never forward an ingest credential to a redirect target.
        return None


def check_url(url):
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("collector URL cannot contain credentials, query, or fragment")
    if not parsed.hostname or parsed.scheme not in ("http", "https"):
        raise ValueError("invalid collector URL")
    local_hosts = {"localhost", "127.0.0.1", "backend"}
    if os.getenv("LOGIQ_ALLOW_LOCAL_HTTP", "").lower() == "true":
        local_hosts.add("host.docker.internal")
    if parsed.scheme == "http" and parsed.hostname not in local_hosts:
        raise ValueError("Remote delivery requires HTTPS")


def validate(kind, record):
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    if not isinstance(record.get("service"), str) or not 1 <= len(record["service"]) <= 128:
        raise ValueError("service is required (maximum 128 characters)")
    if kind == "logs":
        if record.get("level") not in ("info", "warn", "error", "debug", "fatal", "critical"):
            raise ValueError("invalid log level")
        if not isinstance(record.get("message"), str) or len(record["message"]) > 500000:
            raise ValueError("message is required")
    else:
        for key in ("cpu", "memory", "latency"):
            value = record.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {key}")
            if key != "latency" and value > 100:
                raise ValueError(f"{key} must be a percentage")
    if record.get("attrs") is not None and not isinstance(record["attrs"], dict):
        raise ValueError("attrs must be an object")
    record = dict(record)
    record["event_id"] = str(uuid.UUID(record["event_id"])) if record.get("event_id") else str(uuid.uuid4())
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
    return record


class Queue:
    def __init__(self, path, max_events=100000, max_bytes=268435456):
        self.path, self.max_events, self.max_bytes = path, max_events, max_bytes
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, kind TEXT, body TEXT, rejected INTEGER DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS offsets (path TEXT PRIMARY KEY, identity TEXT, offset INTEGER)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, records):
        bodies = [json.dumps(validate(kind, record)) for record in records]
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count, used = db.execute("SELECT count(*), coalesce(sum(length(CAST(body AS BLOB))),0) FROM events").fetchone()
            if count + len(bodies) > self.max_events or used + sum(len(b.encode()) for b in bodies) > self.max_bytes:
                raise OverflowError("collector queue full")
            db.executemany("INSERT INTO events(kind, body) VALUES (?, ?)", [(kind, body) for body in bodies])

    def batch(self, kind, size):
        with self.connect() as db:
            return db.execute("SELECT id, body FROM events WHERE kind=? AND rejected=0 ORDER BY id LIMIT ?", (kind, size)).fetchall()

    def finish(self, ids, rejected=False):
        with self.connect() as db:
            command = "UPDATE events SET rejected=1 WHERE id=?" if rejected else "DELETE FROM events WHERE id=?"
            db.executemany(command, [(i,) for i in ids])

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT rejected, count(*) FROM events GROUP BY rejected").fetchall())

    def tail(self, path, service, limit=100, kind="logs"):
        """Atomically enqueue complete JSONL lines and their durable file cursor."""
        path = os.path.abspath(path)
        with open(path, "rb") as source, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            stat = os.fstat(source.fileno())
            identity = f"{stat.st_dev}:{stat.st_ino}"
            saved = db.execute("SELECT identity, offset FROM offsets WHERE path=?", (path,)).fetchone()
            offset = saved[1] if saved and saved[0] == identity and saved[1] <= stat.st_size else 0
            source.seek(offset)
            count, used = db.execute("SELECT count(*), coalesce(sum(length(CAST(body AS BLOB))),0) FROM events").fetchone()
            added = 0
            for _ in range(limit):
                line = source.readline(1048577)
                if not line or not line.endswith(b"\n"):
                    break
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("JSONL record must be an object")
                    record.setdefault("service", service)
                    if kind == "logs" and "error_severity" in record:
                        severity = str(record.pop("error_severity")).lower()
                        record["level"] = {"log":"info", "notice":"info", "warning":"warn", "panic":"critical"}.get(severity, severity)
                        timestamp = record.pop("timestamp", None)
                        if timestamp:
                            record["timestamp"] = timestamp.replace(" UTC", "+00:00")
                    body = json.dumps(validate(kind, record))
                    rejected = 0
                except (ValueError, TypeError, AttributeError):
                    body, rejected = json.dumps({"raw": line.decode("utf-8", errors="replace")}), 1
                size = len(body.encode())
                if count + 1 > self.max_events or used + size > self.max_bytes:
                    break
                db.execute("INSERT INTO events(kind,body,rejected) VALUES (?,?,?)", (kind, body, rejected))
                offset = source.tell()
                count, used, added = count + 1, used + size, added + 1
            db.execute("INSERT INTO offsets VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET identity=excluded.identity, offset=excluded.offset",
                       (path, identity, offset))
            return added


def deliver(queue, base_url, token, kind, size=100):
    rows = queue.batch(kind, size)
    if not rows:
        return False
    request = Request(f"{base_url.rstrip('/')}/{kind}/bulk",
                      data=json.dumps([json.loads(body) for _, body in rows]).encode(),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            if response.status != 202:
                raise RuntimeError("backend did not accept batch")
    except HTTPError as error:
        if error.code in (400, 413, 422):
            if len(rows) > 1:
                return deliver(queue, base_url, token, kind, 1)
            queue.finish([rows[0][0]], rejected=True)
            log.error("Rejected record preserved in queue (HTTP %s)", error.code)
            return True
        raise
    queue.finish([i for i, _ in rows])
    return True


def make_handler(queue):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            pass

        def reply(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if self.path != "/health":
                return self.reply(404, {"error": "not found"})
            counts = queue.counts()
            self.reply(200, {"status": "ok", "pending": counts.get(0, 0), "rejected": counts.get(1, 0)})

        def do_POST(self):
            kind = self.path.strip("/")
            if kind not in ("logs", "metrics"):
                return self.reply(404, {"error": "not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1048576:
                    return self.reply(413, {"error": "request must be between 1 byte and 1 MiB"})
                data = json.loads(self.rfile.read(size))
                records = data if isinstance(data, list) else [data]
                if not 1 <= len(records) <= 100:
                    raise ValueError("send between 1 and 100 records")
                queue.put(kind, records)
                self.reply(202, {"status": "queued", "count": len(records)})
            except OverflowError as error:
                self.reply(503, {"error": str(error)})
            except (ValueError, TypeError, AttributeError) as error:
                self.reply(400, {"error": str(error)})
            except sqlite3.Error:
                self.reply(503, {"error": "queue unavailable"})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8181)
    parser.add_argument("--queue", default="collector.sqlite3")
    parser.add_argument("--log-file", help="optional application JSONL log file")
    parser.add_argument("--metrics-file", help="optional application JSONL metric file")
    parser.add_argument("--service", default="my-app")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    base_url, token = os.environ["LOGIQ_URL"], os.environ["LOGIQ_INGEST_KEY"]
    check_url(base_url)
    queue, stop = Queue(args.queue), threading.Event()
    agent_file = Path(args.queue).resolve().parent / "agent-id"
    if not agent_file.exists():
        agent_file.write_text(str(uuid.uuid4()))
    agent_id = agent_file.read_text().strip()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(queue))
    server.daemon_threads = True

    def ship():
        delay = 1
        next_heartbeat = 0
        while not stop.is_set():
            try:
                if time.monotonic() >= next_heartbeat:
                    next_heartbeat = time.monotonic() + 30
                    counts = queue.counts()
                    try:
                        heartbeat_request = Request(f"{base_url.rstrip('/')}/agent/heartbeat", data=json.dumps({"agent_id":agent_id,"version":"1.1.0","pending":counts.get(0,0),"rejected":counts.get(1,0)}).encode(), headers={"Content-Type":"application/json","Authorization":f"Bearer {token}"})
                        with build_opener(NoRedirect()).open(heartbeat_request,timeout=5):
                            pass
                    except Exception:
                        log.warning("Heartbeat unavailable; file collection continues")
                sent = deliver(queue, base_url, token, "logs")
                sent = deliver(queue, base_url, token, "metrics") or sent
                delay = 1
                if not sent:
                    stop.wait(1)
            except Exception as error:
                log.warning("Delivery unavailable (%s); queue retained", type(error).__name__)
                stop.wait(delay + random.random())
                delay = min(delay * 2, 60)

    worker = threading.Thread(target=ship, daemon=True)
    def tail_file():
        while not stop.is_set():
            for path, kind in ((args.log_file, "logs"), (args.metrics_file, "metrics")):
                if path:
                    try:
                        queue.tail(path, args.service, kind=kind)
                    except (OSError, sqlite3.Error):
                        log.warning("Source file unavailable; cursor retained")
            stop.wait(1)
    tailer = threading.Thread(target=tail_file, daemon=True) if args.log_file or args.metrics_file else None
    if tailer:
        tailer.start()
    worker.start()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (stop.set(), threading.Thread(target=server.shutdown, daemon=True).start()))
    log.info("Collector listening on localhost:%s", args.port)
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=12)
        if tailer:
            tailer.join(timeout=12)


if __name__ == "__main__":
    main()
