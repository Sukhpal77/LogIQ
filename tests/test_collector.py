import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import pytest
from collector import Queue, deliver, make_handler, check_url

LOG = {"service": "checkout", "level": "error", "message": "database unavailable"}
METRIC = {"service": "checkout", "cpu": 95, "memory": 90, "latency": 1500}


@pytest.fixture
def queue(tmp_path):
    return Queue(str(tmp_path / "queue.sqlite3"))


@pytest.fixture
def backend():
    class Handler(BaseHTTPRequestHandler):
        status = 503
        records = []

        def log_message(self, *_):
            pass

        def do_POST(self):
            self.records.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(self.status)
            self.end_headers()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", Handler
    server.shutdown()
    thread.join()
    server.server_close()


def test_outage_restart_retry_keeps_same_event_id(queue, backend):
    url, handler = backend
    queue.put("logs", [LOG])
    original = queue.batch("logs", 100)
    with pytest.raises(HTTPError):
        deliver(queue, url, "test-token", "logs")
    reopened = Queue(queue.path)
    assert reopened.batch("logs", 100) == original
    handler.status = 202
    assert deliver(reopened, url, "test-token", "logs")
    assert handler.records[0][0]["event_id"] == handler.records[1][0]["event_id"]
    assert not reopened.batch("logs", 100)


def test_auth_failure_preserves_queue(queue, backend):
    url, handler = backend
    handler.status = 401
    queue.put("metrics", [METRIC])
    with pytest.raises(HTTPError):
        deliver(queue, url, "bad-key", "metrics")
    assert queue.counts() == {0: 1}


def test_rejected_record_is_quarantined_not_deleted(queue, backend):
    url, handler = backend
    handler.status = 422
    queue.put("logs", [LOG, LOG])
    assert deliver(queue, url, "key", "logs")
    assert queue.counts() == {0: 1, 1: 1}


def test_queue_limits_are_atomic(tmp_path):
    queue = Queue(str(tmp_path / "small.sqlite3"), max_events=1)
    with pytest.raises(OverflowError):
        queue.put("logs", [LOG, LOG])
    assert queue.counts() == {}


@pytest.mark.parametrize("value", [-1, 101, float("nan"), float("inf"), True])
def test_invalid_metrics_are_rejected(queue, value):
    with pytest.raises(ValueError):
        queue.put("metrics", [{**METRIC, "cpu": value}])
    assert queue.counts() == {}


def test_local_http_accepts_without_central_backend(queue):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(queue))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/logs",
                          data=json.dumps(LOG).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            assert response.status == 202
        assert queue.counts() == {0: 1}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.mark.parametrize("url", ["http://server.example.com", "http://backend:80@evil.example.com", "https://user:pass@server.example.com"])
def test_remote_http_and_embedded_credentials_rejected(url):
    with pytest.raises(ValueError):
        check_url(url)
