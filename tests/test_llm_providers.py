import json
import httpx
import pytest
from app.ai import analyzer, agents
from app.config import settings


def gemini_client(monkeypatch, responder):
    original = httpx.Client
    monkeypatch.setattr(analyzer.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(responder), **kwargs))
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test-google-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "gemini_model", "gemini-test-model")


def test_gemini_json_and_header_auth(monkeypatch):
    def respond(request):
        assert request.url.path.endswith("/gemini-test-model:generateContent")
        assert request.headers["x-goog-api-key"] == "test-google-key"
        assert "key=" not in str(request.url)
        body = json.loads(request.content)
        assert body["systemInstruction"]["parts"][0]["text"] == "system prompt"
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [
            {"text": "hidden reasoning", "thought": True}, {"text": '{"analysis":"Database slow"}'}]}}]})
    gemini_client(monkeypatch, respond)
    assert analyzer.llm_json("system prompt", "user input") == {"analysis": "Database slow"}


@pytest.mark.parametrize("status,payload", [(429, {}), (401, {}), (200, {}),
    (200, {"candidates": [{"content": {"parts": [{"text": "not JSON"}]}}]}),
    (200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})])
def test_gemini_failure_uses_heuristics(monkeypatch, status, payload):
    gemini_client(monkeypatch, lambda request: httpx.Response(status, json=payload))
    assert analyzer.llm_json("system", "input") == {}


def test_disabled_provider_never_calls_api(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "disabled")
    monkeypatch.setattr(settings, "openai_api_key", "still-present")
    monkeypatch.setattr(settings, "gemini_api_key", "still-present")
    assert not settings.llm_configured
    assert analyzer.llm_json("system", "input") == {}


def test_pipeline_runs_with_gemini_without_openai(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_api_key", "")
    responses = iter([{"analysis":"Gemini pipeline ran"}, {"root_cause":"Database slow"}, {"suggestion":"Inspect database"}, {"actions":["notify:slack"]}])
    monkeypatch.setattr(agents,"llm_json",lambda *_:next(responses))
    result=agents.run({"service":"astercart-api","findings":[],"logs":[],"severity":"high"})
    assert result["analysis"]=="Gemini pipeline ran"
    assert result["root_cause"]=="Database slow"
    assert result["actions"]==[]


def test_real_pipeline_runs_without_network(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "disabled")
    result = agents.run({"service": "astercart-api", "findings": [], "logs": [], "severity": "low"})
    assert result["root_cause"] == "System appears healthy."


def test_provider_error_explains_quota_without_exposing_secrets(monkeypatch):
    gemini_client(monkeypatch, lambda request: httpx.Response(429, json={"error":{"message":"private provider contents"}}))
    assert analyzer.llm_json("system", "input") == {}
    assert "429" in analyzer.provider_error.get()
    assert "quota" in analyzer.provider_error.get()
    assert "private" not in analyzer.provider_error.get()
    assert "test-google-key" not in analyzer.provider_error.get()
