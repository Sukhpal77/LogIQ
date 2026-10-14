"""Threshold rule engine — deterministic checks for obvious operational issues."""
from typing import List

from app.config import settings
from app.models.schemas import AnomalyFinding, MetricEntry


def detect(metric: MetricEntry) -> List[AnomalyFinding]:
    thresholds = {"cpu": settings.rule_cpu, "memory": settings.rule_mem, "latency": settings.rule_latency}
    out: List[AnomalyFinding] = []
    for field, threshold in thresholds.items():
        value = float(getattr(metric, field))
        out.append(
            AnomalyFinding(
                field=field,
                value=value,
                score=round(value - threshold, 3),
                method=f"rule(>{threshold})",
                flagged=value > threshold,
            )
        )
    return out
