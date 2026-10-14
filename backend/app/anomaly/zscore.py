"""Z-score detector. Flags latest sample when |z| >= ZSCORE_THRESHOLD."""
from typing import List
import numpy as np

from app.config import settings
from app.models.schemas import AnomalyFinding, MetricEntry

_FIELDS = ("cpu", "memory", "latency")
_MIN_SAMPLES = 5


def detect(metrics: List[MetricEntry]) -> List[AnomalyFinding]:
    if len(metrics) < _MIN_SAMPLES:
        return []
    latest = metrics[-1]
    out: List[AnomalyFinding] = []
    for field in _FIELDS:
        series = np.array([getattr(m, field) for m in metrics], dtype=float)
        std = float(series.std())
        z = 0.0 if std == 0 else (float(getattr(latest, field)) - float(series.mean())) / std
        out.append(
            AnomalyFinding(
                field=field,
                value=float(getattr(latest, field)),
                score=round(float(z), 3),
                method="zscore",
                flagged=abs(z) >= settings.zscore_threshold,
            )
        )
    return out
