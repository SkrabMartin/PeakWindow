"""Ridge regression forecaster: one standardized Ridge model per forecast horizon.

Inputs come only from ``build_features`` (strictly pre-origin load plus the target
calendar). Two feature specifications are available:

* ``additive``: lags, last load, previous-day mean, holiday flags, local hour (which,
  within one horizon, only marks summer vs winter time), weekday and month one-hot
* ``weekday_interactions``: ``additive`` plus ``lag_24h`` and ``lag_168h`` multiplied
  by each weekday indicator, so the weight of yesterday's or last week's load can
  depend on the day type (Monday's "yesterday" is a Sunday)

Fitting is deterministic (closed-form Ridge), so no random seed is involved. A fitted
model exports its scaler and coefficients as plain JSON, without pickling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from peakwindow.contracts import HORIZON_HOURS

SPECS = ("additive", "weekday_interactions")
NUMERIC = ("lag_24h_mw", "lag_168h_mw", "last_load_mw", "prev_24h_mean_mw", "local_hour")
FLAGS = ("is_weekend", "is_holiday", "lag_24h_is_holiday", "lag_168h_is_holiday")
WEEKDAYS = range(7)
MONTHS = range(1, 13)


def design_matrix(frame: pd.DataFrame, spec: str) -> pd.DataFrame:
    """Encode feature rows as a numeric matrix with stable, named columns."""
    if spec not in SPECS:
        raise ValueError(f"unknown feature spec {spec!r}; choose one of {SPECS}")
    columns: dict[str, pd.Series] = {name: frame[name].astype("float64") for name in NUMERIC}
    for name in FLAGS:
        columns[name] = frame[name].astype("float64")
    for day in WEEKDAYS:
        columns[f"weekday_{day}"] = (frame["local_weekday"] == day).astype("float64")
    for month in MONTHS:
        columns[f"month_{month}"] = (frame["month"] == month).astype("float64")
    if spec == "weekday_interactions":
        for day in WEEKDAYS:
            indicator = columns[f"weekday_{day}"]
            columns[f"lag_24h_x_weekday_{day}"] = indicator * columns["lag_24h_mw"]
            columns[f"lag_168h_x_weekday_{day}"] = indicator * columns["lag_168h_mw"]
    return pd.DataFrame(columns, index=frame.index)


@dataclass
class RidgeForecaster:
    """24 independent ``StandardScaler → Ridge(alpha)`` pipelines, one per horizon."""

    alpha: float
    spec: str = "additive"
    _models: dict[int, Pipeline] = field(default_factory=dict, init=False, repr=False)
    _columns: tuple[str, ...] = field(default=(), init=False, repr=False)

    def fit(self, frame: pd.DataFrame, target: pd.Series) -> RidgeForecaster:
        matrix = design_matrix(frame, self.spec)
        self._columns = tuple(matrix.columns)
        self._models = {}
        for horizon in range(HORIZON_HOURS):
            rows = (frame["horizon_h"] == horizon).to_numpy()
            if not rows.any():
                raise ValueError(f"no training rows for horizon {horizon}")
            pipeline = Pipeline([("scale", StandardScaler()), ("ridge", Ridge(alpha=self.alpha))])
            pipeline.fit(matrix.to_numpy()[rows], target.to_numpy()[rows])
            self._models[horizon] = pipeline
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if not self._models:
            raise RuntimeError("RidgeForecaster is not fitted")
        matrix = design_matrix(frame, self.spec).to_numpy()
        predicted = np.full(len(frame), np.nan)
        horizons = frame["horizon_h"].to_numpy()
        for horizon, pipeline in self._models.items():
            rows = horizons == horizon
            if rows.any():
                predicted[rows] = pipeline.predict(matrix[rows])
        return predicted

    def export(self) -> dict[str, Any]:
        """Everything needed to reproduce predictions: columns, scaling, coefficients."""
        if not self._models:
            raise RuntimeError("RidgeForecaster is not fitted")
        horizons = []
        for horizon, pipeline in sorted(self._models.items()):
            scaler: StandardScaler = pipeline.named_steps["scale"]
            ridge: Ridge = pipeline.named_steps["ridge"]
            horizons.append(
                {
                    "horizon_h": horizon,
                    "scaler_mean": [float(v) for v in scaler.mean_],
                    "scaler_scale": [float(v) for v in scaler.scale_],
                    "coefficients": [float(v) for v in ridge.coef_],
                    "intercept": float(ridge.intercept_),
                }
            )
        return {
            "model": "ridge_per_horizon",
            "alpha": self.alpha,
            "spec": self.spec,
            "columns": list(self._columns),
            "horizons": horizons,
        }
