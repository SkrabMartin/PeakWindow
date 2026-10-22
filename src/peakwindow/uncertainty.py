"""Split-conformal prediction intervals, calibrated separately for each horizon.

For every horizon ``h`` and nominal level ``1 - a``, the half-width ``q_h`` is the
``ceil((n + 1)(1 - a))``-th smallest absolute residual ``|actual - predicted|`` of
the ``n`` calibration rows at that horizon. The interval is ``predicted ± q_h``.

If calibration and test errors were exchangeable, the interval would cover the
actual value with probability at least ``1 - a``. Consecutive years are not
exchangeable (seasons, COVID-19), so coverage is *measured* on the holdout rather
than assumed.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from peakwindow.contracts import HORIZON_HOURS

LEVELS = (0.8, 0.9)


def conformal_quantile(absolute_residuals: np.ndarray, level: float) -> float:
    """The split-conformal half-width for ``level`` from ``n`` absolute residuals."""
    if not 0 < level < 1:
        raise ValueError(f"level must be between 0 and 1, got {level}")
    ordered = np.sort(np.asarray(absolute_residuals, dtype="float64"))
    n = len(ordered)
    rank = math.ceil((n + 1) * level)
    if n == 0 or rank > n:
        raise ValueError(f"{n} calibration residuals are too few for level {level}")
    return float(ordered[rank - 1])


@dataclass(frozen=True)
class ConformalIntervals:
    """Per-horizon half-widths for each nominal level."""

    half_widths: dict[float, tuple[float, ...]]
    calibration_rows_per_horizon: tuple[int, ...]

    @classmethod
    def calibrate(
        cls,
        horizons: np.ndarray,
        actual: np.ndarray,
        predicted: np.ndarray,
        levels: Sequence[float] = LEVELS,
    ) -> ConformalIntervals:
        absolute = np.abs(np.asarray(actual, dtype="float64") - predicted)
        counts = []
        widths: dict[float, list[float]] = {level: [] for level in levels}
        for horizon in range(HORIZON_HOURS):
            rows = horizons == horizon
            counts.append(int(rows.sum()))
            for level in levels:
                widths[level].append(conformal_quantile(absolute[rows], level))
        return cls({level: tuple(values) for level, values in widths.items()}, tuple(counts))

    def bounds(
        self, horizons: np.ndarray, predicted: np.ndarray, level: float
    ) -> tuple[np.ndarray, np.ndarray]:
        half = np.asarray(self.half_widths[level])[np.asarray(horizons, dtype="int64")]
        return predicted - half, predicted + half


@dataclass(frozen=True)
class CoverageMetrics:
    level: float
    coverage: float
    mean_width_mw: float
    coverage_by_horizon: tuple[float, ...]
    coverage_weekday: float | None
    coverage_weekend: float | None
    coverage_holiday: float | None
    below_lower: float
    above_upper: float

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["coverage_by_horizon"] = list(self.coverage_by_horizon)
        return data


def _share(flags: pd.Series, mask: pd.Series | None = None) -> float | None:
    selected = flags if mask is None else flags[mask]
    return round(float(selected.mean()), 4) if len(selected) else None


def coverage_metrics(
    frame: pd.DataFrame,
    actual: np.ndarray,
    predicted: np.ndarray,
    intervals: ConformalIntervals,
    level: float,
) -> CoverageMetrics:
    """Empirical coverage and width of the ``level`` interval on ``frame``'s rows."""
    horizons = frame["horizon_h"].to_numpy()
    lower, upper = intervals.bounds(horizons, predicted, level)
    inside = pd.Series((actual >= lower) & (actual <= upper), index=frame.index)
    weekend = frame["is_weekend"].astype(bool)
    holiday = frame["is_holiday"].astype(bool)
    by_horizon = inside.groupby(frame["horizon_h"]).mean()
    return CoverageMetrics(
        level=level,
        coverage=round(float(inside.mean()), 4),
        mean_width_mw=round(float(np.mean(upper - lower)), 3),
        coverage_by_horizon=tuple(round(float(value), 4) for value in by_horizon),
        coverage_weekday=_share(inside, ~weekend),
        coverage_weekend=_share(inside, weekend),
        coverage_holiday=_share(inside, holiday) if holiday.any() else None,
        below_lower=round(float(np.mean(actual < lower)), 4),
        above_upper=round(float(np.mean(actual > upper)), 4),
    )
