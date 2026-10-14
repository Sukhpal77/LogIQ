"""LLM call helper used by the AI agents.

Wraps OpenAI chat completions with a JSON-only response format and a clean
mock fallback so the rest of the system never has to worry about the LLM
being unreachable.
"""
import json
import logging
from contextvars import ContextVar
from urllib.parse import quote
import httpx
from typing import Any, Dict, List

from app.config import settings
from app.models.schemas import AnomalyFinding, LogEntry
from app.ai.metering import meter, allow_provider, record

log = logging.getLogger(__name__)
provider_error = ContextVar("provider_error", default="")


def llm_json(system: str, user: str) -> Dict[str, Any]:
    """Call the configured LLM and parse the JSON it returns. Returns {} on failure."""
    provider_error.set("")
    if not settings.llm_configured or not allow_provider.get():
        return {}
    usage = meter.get()
    if usage is not None:
        if usage["calls"] >= 4:
            return {}
        usage["calls"] += 1
    user = user[:24000]
    try:
        if settings.llm_provider == "gemini":
            model = quote(settings.gemini_model.removeprefix("models/"), safe="")
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": settings.gemini_api_key},
                    json={"systemInstruction": {"parts": [{"text": system}]},
                          "contents": [{"role": "user", "parts": [{"text": user}]}],
                          "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2, "maxOutputTokens": 2048}})
                response.raise_for_status()
                candidates = response.json().get("candidates") or []
                parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
                content = "".join(part.get("text", "") for part in parts if not part.get("thought"))
                data = json.loads(content or "{}")
                metadata = response.json().get("usageMetadata", {})
                record(metadata.get("promptTokenCount", 0), metadata.get("candidatesTokenCount", 0))
                return data if isinstance(data, dict) else {}
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=15.0, max_retries=1)
        with client:
            resp = client.chat.completions.create(
                model=settings.openai_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0.2,
                max_tokens=2048,
            )
        data = json.loads(resp.choices[0].message.content or "{}")
        reported = getattr(resp, "usage", None)
        record(getattr(reported, "prompt_tokens", 0), getattr(reported, "completion_tokens", 0))
        return data if isinstance(data, dict) else {}
    except Exception as error:
        if isinstance(error, httpx.HTTPStatusError):
            code = error.response.status_code
            reason = {400: "Invalid key or request settings.", 401: "API key was rejected.", 403: "API key permissions or regional access were denied.", 404: "Model is unavailable for this API key.", 429: "Provider quota or rate limit exceeded."}.get(code, "Provider service returned an error.")
            provider_error.set(f"HTTP {code}: {reason}")
        elif isinstance(error, httpx.TimeoutException):
            provider_error.set("Provider request timed out. Retry or check server connectivity.")
        elif isinstance(error, httpx.RequestError):
            provider_error.set("Cannot connect to the provider. Check server network access.")
        elif isinstance(error, json.JSONDecodeError):
            provider_error.set("Provider returned incomplete or invalid JSON. Retry the connection test.")
        else:
            provider_error.set("Provider request failed. Check credentials and model access.")
        log.warning("%s LLM unavailable (%s); using heuristics", settings.llm_provider, type(error).__name__)
        return {}


def heuristic_summary(findings: List[AnomalyFinding], logs: List[LogEntry]) -> Dict[str, str]:
    """Deterministic fallback used when no LLM is configured."""
    flagged = [f for f in findings if f.flagged]
    if not flagged and not logs:
        return {
            "reason": "No anomalies detected.",
            "root_cause": "System appears healthy.",
            "suggestion": "Continue monitoring.",
        }
    reasons, causes, suggestions = [], [], []
    for f in flagged:
        if f.field == "cpu":
            reasons.append(f"High CPU ({f.value:.1f}%)")
            causes.append("Possible CPU-bound workload, runaway thread, or heavy DB query")
            suggestions.append("Inspect top processes; profile slow queries; scale horizontally")
        elif f.field == "memory":
            reasons.append(f"High memory ({f.value:.1f}%)")
            causes.append("Likely memory leak or undersized instance")
            suggestions.append("Capture heap dump; restart service; review recent deploys")
        elif f.field == "latency":
            reasons.append(f"Elevated latency ({f.value:.0f} ms)")
            causes.append("Downstream slowdown or saturated thread pool")
            suggestions.append("Check downstream health; review pool sizing")
        elif f.method == "isolation_forest":
            reasons.append("Multivariate metric pattern unusual")
            causes.append("Combined CPU/memory/latency deviates from learned normal")
            suggestions.append("Correlate with deploys and traffic spikes")
    if logs and not reasons:
        reasons.append(f"{len(logs)} recent error log(s)")
        causes.append(f"e.g. '{logs[-1].message}' from '{logs[-1].service}'")
        suggestions.append("Investigate failing service")
    return {
        "reason": "; ".join(reasons) or "Anomalous signal detected",
        "root_cause": "; ".join(causes) or "Unknown",
        "suggestion": "; ".join(suggestions) or "Investigate recent changes",
    }
