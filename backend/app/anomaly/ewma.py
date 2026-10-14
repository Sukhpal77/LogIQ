"""Exponentially-Weighted Moving Average detector for trend detection.

Flags when |x_t - EWMA_t| / sigma_ewma > EWMA_THRESHOLD. Catches gradual
drifts that pure z-score over the full window can dilute.
"""
from typing import List
import numpy as np

from app.config import settings
from app.models.schemas import AnomalyFinding, MetricEntry

_FIELDS = ("cpu", "memory", "latency")
_MIN_SAMPLES = 8


def _ewma(series: np.ndarray, alpha: float) -> np.ndarray:
    out = np.empty_like(series)
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = alpha * series[i] + (1.0 - alpha) * out[i - 1]
    return out


def detect(metrics: List[MetricEntry]) -> List[AnomalyFinding]:
    if len(metrics) < _MIN_SAMPLES:
        return []
    out: List[AnomalyFinding] = []
    for field in _FIELDS:
        series = np.array([getattr(m, field) for m in metrics], dtype=float)
        ewma = _ewma(series, settings.ewma_alpha)
        residuals = series[:-1] - ewma[:-1]
        sigma = float(residuals.std()) or 1.0
        latest = float(series[-1])
        deviation = (latest - float(ewma[-2])) / sigma
        out.append(
            AnomalyFinding(
                field=field,
                value=latest,
                score=round(float(deviation), 3),
                method="ewma",
                flagged=abs(deviation) >= settings.ewma_threshold,
            )
        )
    return out
