import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def demo(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_LOG_FILE", str(tmp_path / "app.jsonl"))
    monkeypatch.setenv("APP_METRICS_FILE", str(tmp_path / "metrics.jsonl"))
    path = Path(__file__).resolve().parents[2] / "demo-three-tier" / "api" / "app.py"
    spec = importlib.util.spec_from_file_location("demo_test_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scenario,status,level", [
    ("debug", 200, "debug"), ("warning", 400, "warn"),
    ("api-error", 500, "error"), ("critical", 503, "critical")])
def test_scenario_writes_matching_standard_log(demo, scenario, status, level):
    code, body = demo.scenario(scenario, "trace-123")
    assert code == status
    record = json.loads(Path(demo.LOG_PATH).read_text().splitlines()[-1])
    assert record["level"] == level
    assert record["attrs"]["trace_id"] == "trace-123"
    assert record["service"] == "astercart-api"


def test_database_error_and_slow_query(demo, monkeypatch):
    def failed(query):
        raise demo.psycopg2.ProgrammingError("missing table")
    monkeypatch.setattr(demo, "database", failed)
    assert demo.scenario("database-error", "trace")[0] == 500
    calls = []
    monkeypatch.setattr(demo, "database", lambda query: calls.append(query))
    assert demo.scenario("slow-database", "trace")[0] == 200
    assert calls == ["SELECT pg_sleep(1.6)"]


def test_synthetic_spike_and_reset(demo):
    assert demo.scenario("metric-spike", "trace")[0] == 200
    metrics = json.loads(Path(demo.METRIC_PATH).read_text().splitlines()[0])
    assert metrics["cpu"] == 98 and metrics["latency"] == 2200
    assert demo.synthetic_until > demo.time.monotonic()
    assert demo.scenario("reset", "trace")[0] == 200
    assert demo.synthetic_until == 0
