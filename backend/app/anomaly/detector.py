"""Anomaly detection orchestrator — combines rule, z-score, EWMA, isolation forest."""
from typing import List, Tuple

from app.anomaly import ewma, isolation, rules, zscore
from app.models.schemas import AnomalyFinding, MetricEntry


def severity(findings: List[AnomalyFinding]) -> str:
    flagged = [f for f in findings if f.flagged]
    if not flagged:
        return "low"
    methods = {f.method for f in flagged}
    # If multiple independent methods agree, treat as more severe.
    if len(methods) >= 3:
        return "critical"
    if len(methods) == 2:
        return "high"
    return "medium"


def run(metrics: List[MetricEntry]) -> Tuple[bool, List[AnomalyFinding], str]:
    if not metrics:
        return False, [], "low"
    findings: List[AnomalyFinding] = []
    findings.extend(rules.detect(metrics[-1]))
    findings.extend(zscore.detect(metrics))
    findings.extend(ewma.detect(metrics))
    findings.extend(isolation.detect(metrics))
    is_anomaly = any(f.flagged for f in findings)
    return is_anomaly, findings, severity(findings)
