"""Investigation pipeline: analyzer, root cause and recommendations.

The same bounded stages use a configured provider or deterministic fallback.
"""
import json
import logging
from typing import Any, Dict, List, TypedDict

from app.ai.analyzer import heuristic_summary, llm_json
from app.config import settings
from app.models.schemas import AnomalyFinding, LogEntry

log = logging.getLogger(__name__)


class BrainState(TypedDict, total=False):
    service: str
    findings: List[Dict[str, Any]]
    logs: List[Dict[str, Any]]
    similar_incidents: List[str]
    dependencies: List[str]
    severity: str

    # outputs progressively filled by each agent
    analysis: str
    root_cause: str
    suggestion: str
    actions: List[str]


# ----- Per-agent functions -----

def _serialize(state: BrainState) -> str:
    return json.dumps(
        {
            "service": state.get("service"),
            "severity": state.get("severity"),
            "findings": [f for f in state.get("findings", []) if f.get("flagged")],
            "recent_error_logs": state.get("logs", []),
            "dependencies": state.get("dependencies", []),
            "similar_incidents": state.get("similar_incidents", []),
        },
        indent=2,
        default=str,
    )


def analyzer_agent(state: BrainState) -> BrainState:
    """Summarize what is happening across logs + metrics in one paragraph."""
    sys = (
        "You are a senior SRE analyst. Read the anomaly findings and recent error logs "
        "and write a single short paragraph (<=60 words) describing what is happening. "
        "Return JSON: {\"analysis\": ...}."
    )
    data = llm_json(sys, _serialize(state))
    if data.get("analysis"):
        state["analysis"] = str(data["analysis"]).strip()
    else:
        findings = [AnomalyFinding(**f) for f in state.get("findings", [])]
        logs = [LogEntry(**l) for l in state.get("logs", [])]
        state["analysis"] = heuristic_summary(findings, logs)["reason"]
    return state


def root_cause_agent(state: BrainState) -> BrainState:
    sys = (
        "You are a senior SRE diagnosing an incident. Given the analysis, dependencies, "
        "and similar past incidents, identify the most likely root cause in one sentence. "
        "Return JSON: {\"root_cause\": ...}."
    )
    user = json.dumps({"analysis": state.get("analysis"), **state}, default=str)
    data = llm_json(sys, user)
    if data.get("root_cause"):
        state["root_cause"] = str(data["root_cause"]).strip()
    else:
        findings = [AnomalyFinding(**f) for f in state.get("findings", [])]
        logs = [LogEntry(**l) for l in state.get("logs", [])]
        state["root_cause"] = heuristic_summary(findings, logs)["root_cause"]
    return state


def decision_agent(state: BrainState) -> BrainState:
    sys = (
        "You are an SRE on-call. Decide the highest-leverage remediation. "
        "Return JSON: {\"suggestion\": <one sentence>}."
    )
    user = json.dumps(
        {"analysis": state.get("analysis"), "root_cause": state.get("root_cause"), "severity": state.get("severity")},
        default=str,
    )
    data = llm_json(sys, user)
    if data.get("suggestion"):
        state["suggestion"] = str(data["suggestion"]).strip()
    else:
        findings = [AnomalyFinding(**f) for f in state.get("findings", [])]
        logs = [LogEntry(**l) for l in state.get("logs", [])]
        state["suggestion"] = heuristic_summary(findings, logs)["suggestion"]
    return state



def run(state: BrainState) -> BrainState:
    """Run the three bounded investigation stages without serialization frameworks."""
    result = decision_agent(root_cause_agent(analyzer_agent(state)))
    result["actions"] = []
    return result
