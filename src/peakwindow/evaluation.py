"""Chronological evaluation of forecast models over the origins of one split period.

Fairness rule: an origin is used only if all 24 targets have a value **and** every
feature is present, so all models are scored on exactly the same origins and target
hours. Other origins are reported as data-gap exceptions with their reasons;
nothing is filled.

Metrics (all in MW unless stated):

* ``mae``, ``rmse``
* ``nmae_pct``: MAE / mean actual load over the same targets, in percent
* ``relative_mae``: MAE / MAE of the weekly seasonal naive on the same targets
* ``abs_error_p50`` / ``p90`` / ``p99``: distribution of absolute errors
* ``mae_by_horizon``: 24 values, ``h = 0..23``
* ``mae_weekday`` / ``mae_weekend`` / ``mae_holiday``: by the target's local calendar
* ``worst_origins``: the five origins with the highest daily MAE

The final holdout is locked: ``build_dataset`` refuses holdout origins unless
``allow_holdout=True``, which only the final evaluation passes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import numpy as np
import pandas as pd

from peakwindow import baselines
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN, ForecastRequest, target_timestamps
from peakwindow.features import FEATURE_COLUMNS, MAX_LOOKBACK, build_features
from peakwindow.splits import SPLIT_V1, SPLIT_VERSION, Period, origins_for

WORST_ORIGINS = 5
ACTUAL = "actual"


class HoldoutLockedError(RuntimeError):
    """Raised when the final holdout is requested before it is unlocked."""


class Model(Protocol):
    """Anything that predicts ``load_mw`` for every row of a dataset frame."""

    def predict(self, frame: pd.DataFrame) -> np.ndarray: ...


@dataclass(frozen=True)
class BaselineModel:
    name: str

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return np.asarray(baselines.predict(frame, self.name), dtype="float64")


@dataclass(frozen=True)
class OriginException:
    origin_utc: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Dataset:
    """Feature rows plus the observed ``actual`` load for every usable origin."""

    frame: pd.DataFrame
    origins_requested: int
    exceptions: tuple[OriginException, ...]

    @property
    def origins_used(self) -> int:
        return self.origins_requested - len(self.exceptions)


@dataclass(frozen=True)
class ModelMetrics:
    mae: float
    rmse: float
    nmae_pct: float
    relative_mae: float
    abs_error_p50: float
    abs_error_p90: float
    abs_error_p99: float
    mae_by_horizon: tuple[float, ...]
    mae_weekday: float | None
    mae_weekend: float | None
    mae_holiday: float | None
    worst_origins: tuple[tuple[str, float], ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mae_by_horizon"] = list(self.mae_by_horizon)
        data["worst_origins"] = [
            {"origin_utc": origin, "mae": mae} for origin, mae in self.worst_origins
        ]
        return data


@dataclass(frozen=True)
class EvaluationResult:
    split_version: str
    period: str
    first_origin_utc: str
    last_origin_utc: str
    origins_in_period: int
    origins_evaluated: int
    target_hours_evaluated: int
    exceptions: tuple[OriginException, ...]
    models: dict[str, ModelMetrics]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["exceptions"] = [
            {"origin_utc": item.origin_utc, "reasons": list(item.reasons)}
            for item in self.exceptions
        ]
        data["models"] = {name: metrics.to_dict() for name, metrics in self.models.items()}
        return data


def _format_utc(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _round(value: float) -> float:
    return round(value, 3)


def actual_values(series: pd.DataFrame | pd.Series, request: ForecastRequest) -> list[float]:
    """The observed load at the 24 targets (NaN where missing). Used for scoring only.

    ``series`` is the canonical frame or a load Series already indexed by timestamp.
    """
    by_time = (
        series if isinstance(series, pd.Series) else series.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    )
    values: list[float] = by_time.reindex(target_timestamps(request)).astype("float64").tolist()
    return values


def build_dataset(
    series: pd.DataFrame, periods: tuple[Period, ...], *, allow_holdout: bool = False
) -> Dataset:
    """Features and actuals for every usable origin of ``periods`` (in order)."""
    if Period.HOLDOUT in periods and not allow_holdout:
        raise HoldoutLockedError(
            "the final holdout is locked until all models are frozen; "
            "use train, validation or calibration instead"
        )
    ordered = series.sort_values(TIMESTAMP_COLUMN, kind="stable").reset_index(drop=True)
    stamps = ordered[TIMESTAMP_COLUMN]
    by_time = ordered.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    rows: list[pd.DataFrame] = []
    exceptions: list[OriginException] = []
    requested = 0
    for period in periods:
        for origin in origins_for(period):
            requested += 1
            request = ForecastRequest(origin_utc=origin)
            # Only the lookback window is passed on; features are identical (tested).
            start = int(stamps.searchsorted(origin - MAX_LOOKBACK, side="left"))
            stop = int(stamps.searchsorted(origin, side="left"))
            features = build_features(ordered.iloc[start:stop], request)
            actual = actual_values(by_time, request)
            reasons = []
            if features[list(FEATURE_COLUMNS)].isna().any().any():
                reasons.append("missing_input")
            if any(math.isnan(value) for value in actual):
                reasons.append("missing_target")
            if reasons:
                exceptions.append(OriginException(_format_utc(origin), tuple(reasons)))
            else:
                rows.append(features.assign(**{ACTUAL: actual}))
    if not rows:
        names = ", ".join(period.value for period in periods)
        raise ValueError(f"no evaluable origins in period(s) {names}")
    return Dataset(pd.concat(rows, ignore_index=True), requested, tuple(exceptions))


def _optional_mean(values: pd.Series, mask: pd.Series) -> float | None:
    return _round(float(values[mask].mean())) if mask.any() else None


def model_metrics(frame: pd.DataFrame, predicted: np.ndarray, reference_mae: float) -> ModelMetrics:
    """Metrics of ``predicted`` against ``frame[actual]`` (see module docstring)."""
    error = pd.Series(predicted, index=frame.index) - frame[ACTUAL]
    absolute = error.abs()
    mae = float(absolute.mean())
    by_horizon = absolute.groupby(frame["horizon_h"]).mean()
    daily = absolute.groupby(frame["origin_utc"]).mean().sort_values(ascending=False, kind="stable")
    weekend = frame["is_weekend"].astype(bool)
    holiday = frame["is_holiday"].astype(bool)
    return ModelMetrics(
        mae=_round(mae),
        rmse=_round(math.sqrt(float((error**2).mean()))),
        nmae_pct=_round(100 * mae / float(frame[ACTUAL].mean())),
        relative_mae=_round(mae / reference_mae),
        abs_error_p50=_round(float(absolute.quantile(0.5))),
        abs_error_p90=_round(float(absolute.quantile(0.9))),
        abs_error_p99=_round(float(absolute.quantile(0.99))),
        mae_by_horizon=tuple(_round(float(value)) for value in by_horizon),
        mae_weekday=_optional_mean(absolute, ~weekend),
        mae_weekend=_optional_mean(absolute, weekend),
        mae_holiday=_optional_mean(absolute, holiday),
        worst_origins=tuple(
            (_format_utc(pd.Timestamp(str(origin))), _round(float(value)))
            for origin, value in daily.head(WORST_ORIGINS).items()
        ),
    )


def score_models(frame: pd.DataFrame, models: Mapping[str, Model]) -> dict[str, ModelMetrics]:
    """Score every model on ``frame``; relative MAE uses the weekly seasonal naive."""
    reference = BaselineModel(baselines.REFERENCE_BASELINE).predict(frame)
    reference_mae = float(np.mean(np.abs(reference - frame[ACTUAL].to_numpy())))
    return {
        name: model_metrics(frame, model.predict(frame), reference_mae)
        for name, model in models.items()
    }


def baseline_models() -> dict[str, Model]:
    return {name: BaselineModel(name) for name in baselines.BASELINES}


def evaluate(
    series: pd.DataFrame,
    period: Period,
    models: Mapping[str, Model],
    *,
    allow_holdout: bool = False,
) -> EvaluationResult:
    dataset = build_dataset(series, (period,), allow_holdout=allow_holdout)
    return evaluate_dataset(dataset, period, models)


def evaluate_dataset(
    dataset: Dataset, period: Period, models: Mapping[str, Model]
) -> EvaluationResult:
    """Score ``models`` on a dataset already built for ``period``."""
    period_range = SPLIT_V1[period]
    return EvaluationResult(
        split_version=SPLIT_VERSION,
        period=period.value,
        first_origin_utc=f"{period_range.first_origin}T00:00:00Z",
        last_origin_utc=f"{period_range.last_origin}T00:00:00Z",
        origins_in_period=dataset.origins_requested,
        origins_evaluated=dataset.origins_used,
        target_hours_evaluated=len(dataset.frame),
        exceptions=dataset.exceptions,
        models=score_models(dataset.frame, models),
    )


def evaluate_baselines(
    series: pd.DataFrame, period: Period, *, allow_holdout: bool = False
) -> EvaluationResult:
    """Score the seasonal naive baselines on ``period``."""
    return evaluate(series, period, baseline_models(), allow_holdout=allow_holdout)
