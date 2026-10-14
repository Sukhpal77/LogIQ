"""Pydantic schemas for API requests and responses."""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, Field


# ----- Auth -----

class LoginRequest(BaseModel):
    username: str
    password: str
    code: str = Field(default="", max_length=64)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str


# ----- Logs / Metrics -----

class LogEntry(BaseModel):
    event_id: Optional[UUID] = None
    service: str = Field(min_length=1, max_length=128)
    level: str = Field(min_length=1, max_length=16, description="info | warn | error | debug")
    message: str = Field(max_length=500000)
    timestamp: Optional[datetime] = None
    attrs: Optional[Dict[str, Any]] = None


class MetricEntry(BaseModel):
    event_id: Optional[UUID] = None
    cpu: float = Field(ge=0, le=100, allow_inf_nan=False)
    memory: float = Field(ge=0, le=100, allow_inf_nan=False)
    latency: float = Field(ge=0, allow_inf_nan=False)
    service: str = Field(default="default", min_length=1, max_length=128)
    timestamp: Optional[datetime] = None


# ----- Anomaly / Analysis -----

class AnomalyFinding(BaseModel):
    field: str
    value: float
    score: float
    method: str
    flagged: bool


class AnalysisResponse(BaseModel):
    mode: str = "heuristic"
    provider: str = "disabled/fallback"
    anomaly: bool
    severity: str = "low"
    reason: str
    root_cause: str
    suggestion: str
    actions: List[str] = []
    findings: List[AnomalyFinding] = []
    recent_error_logs: List[LogEntry] = []
    incident_id: Optional[int] = None


# ----- Incidents -----

class IncidentOut(BaseModel):
    id: int
    service: str
    summary: str
    reason: str
    root_cause: str
    suggestion: str
    severity: str
    status: str
    actions_taken: Optional[Dict[str, Any]] = None
    feedback: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class IncidentFeedback(BaseModel):
    feedback: str = Field(min_length=1, max_length=4000)
    status: Optional[Literal["open", "resolved", "dismissed"]] = None


# ----- Actions -----

class ActionResult(BaseModel):
    kind: str
    target: str
    success: bool
    detail: str


# ----- Context graph -----

class ServiceNode(BaseModel):
    name: str
    kind: str = "app"
    metadata: Optional[Dict[str, Any]] = None


class DependencyEdge(BaseModel):
    source: str
    target: str
    relation: str = "calls"
