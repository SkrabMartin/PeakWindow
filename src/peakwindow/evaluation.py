"""Chronological evaluation of baselines over the origins of one split period.

Fairness rule: an origin is evaluated only if all 24 targets have a value **and**
every feature is present, so all models are scored on exactly the same origins
and target hours. Other origins are reported as data-gap exceptions with their
reasons; nothing is filled.

Metrics (all in MW unless stated):

* ``mae``, ``rmse``
* ``nmae_pct``: MAE / mean actual load over the same targets, in percent
* ``relative_mae``: MAE / MAE of the weekly seasonal naive on the same targets
* ``mae_by_horizon``: 24 values, ``h = 0..23``
* ``mae_weekday`` / ``mae_weekend``: by the target's Europe/Prague calendar
* ``worst_origins``: the five origins with the highest daily MAE

The final holdout is locked: evaluating it requires ``allow_holdout=True``, which
no CLI command passes before the final evaluation phase.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from peakwindow import baselines
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN, ForecastRequest, target_timestamps
from peakwindow.features import FEATURE_COLUMNS, build_features
from peakwindow.splits import SPLIT_V1, SPLIT_VERSION, Period, origins_for

WORST_ORIGINS = 5


class HoldoutLockedError(RuntimeError):
    """Raised when the final holdout is requested before it is unlocked."""


@dataclass(frozen=True)
class OriginException:
    origin_utc: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ModelMetrics:
    mae: float
    rmse: float
    nmae_pct: float
    relative_mae: float
    mae_by_horizon: tuple[float, ...]
    mae_weekday: float | None
    mae_weekend: float | None
    worst_origins: tuple[tuple[str, float], ...]


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
        for name, metrics in self.models.items():
            data["models"][name]["mae_by_horizon"] = list(metrics.mae_by_horizon)
            data["models"][name]["worst_origins"] = [
                {"origin_utc": origin, "mae": mae} for origin, mae in metrics.worst_origins
            ]
        return data


def _format_utc(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def actual_values(series: pd.DataFrame, request: ForecastRequest) -> list[float]:
    """The observed load at the 24 targets (NaN where missing). Used for scoring only."""
    by_time = series.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    values: list[float] = by_time.reindex(target_timestamps(request)).astype("float64").tolist()
    return values


def _round(value: float) -> float:
    return round(value, 3)


def _metrics(scored: pd.DataFrame, model: str, reference_mae: float) -> ModelMetrics:
    error = scored[model] - scored["actual"]
    absolute = error.abs()
    mae = float(absolute.mean())
    by_horizon = absolute.groupby(scored["horizon_h"]).mean()
    by_weekend = absolute.groupby(scored["is_weekend"]).mean()
    daily = (
        absolute.groupby(scored["origin_utc"]).mean().sort_values(ascending=False, kind="stable")
    )
    return ModelMetrics(
        mae=_round(mae),
        rmse=_round(math.sqrt(float((error**2).mean()))),
        nmae_pct=_round(100 * mae / float(scored["actual"].mean())),
        relative_mae=_round(mae / reference_mae),
        mae_by_horizon=tuple(_round(float(value)) for value in by_horizon),
        mae_weekday=_round(float(by_weekend[False])) if False in by_weekend.index else None,
        mae_weekend=_round(float(by_weekend[True])) if True in by_weekend.index else None,
        worst_origins=tuple(
            (_format_utc(pd.Timestamp(str(origin))), _round(float(value)))
            for origin, value in daily.head(WORST_ORIGINS).items()
        ),
    )


def evaluate_baselines(
    series: pd.DataFrame, period: Period, *, allow_holdout: bool = False
) -> EvaluationResult:
    """Score every baseline on the origins of ``period`` (see module docstring)."""
    if period is Period.HOLDOUT and not allow_holdout:
        raise HoldoutLockedError(
            "the final holdout is locked until all models are frozen; "
            "evaluate train, validation or calibration instead"
        )

    rows: list[pd.DataFrame] = []
    exceptions: list[OriginException] = []
    origins = origins_for(period)
    for origin in origins:
        request = ForecastRequest(origin_utc=origin)
        features = build_features(series, request)
        actual = actual_values(series, request)
        reasons = []
        if features[list(FEATURE_COLUMNS)].isna().any().any():
            reasons.append("missing_input")
        if any(math.isnan(value) for value in actual):
            reasons.append("missing_target")
        if reasons:
            exceptions.append(OriginException(_format_utc(origin), tuple(reasons)))
            continue
        frame = features.assign(actual=actual)
        for name in baselines.BASELINES:
            frame[name] = baselines.predict(features, name)
        rows.append(frame)

    if not rows:
        raise ValueError(f"no evaluable origins in period {period.value!r}")
    scored = pd.concat(rows, ignore_index=True)
    reference = float((scored[baselines.REFERENCE_BASELINE] - scored["actual"]).abs().mean())
    models = {name: _metrics(scored, name, reference) for name in baselines.BASELINES}
    period_range = SPLIT_V1[period]
    return EvaluationResult(
        split_version=SPLIT_VERSION,
        period=period.value,
        first_origin_utc=f"{period_range.first_origin}T00:00:00Z",
        last_origin_utc=f"{period_range.last_origin}T00:00:00Z",
        origins_in_period=len(origins),
        origins_evaluated=len(rows),
        target_hours_evaluated=len(scored),
        exceptions=tuple(exceptions),
        models=models,
    )
