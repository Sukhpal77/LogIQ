"""Isolation Forest over the (cpu, memory, latency) vector."""
from typing import List

import numpy as np
from sklearn.ensemble import IsolationForest

from app.models.schemas import AnomalyFinding, MetricEntry

_MIN_SAMPLES = 10


def detect(metrics: List[MetricEntry]) -> List[AnomalyFinding]:
    if len(metrics) < _MIN_SAMPLES:
        return []
    X = np.array([[m.cpu, m.memory, m.latency] for m in metrics], dtype=float)
    model = IsolationForest(contamination="auto", random_state=42)
    model.fit(X)
    latest = X[-1].reshape(1, -1)
    pred = int(model.predict(latest)[0])
    score = float(model.decision_function(latest)[0])
    return [
        AnomalyFinding(
            field="vector(cpu,memory,latency)",
            value=float(np.linalg.norm(latest)),
            score=round(score, 4),
            method="isolation_forest",
            flagged=pred == -1,
        )
    ]
