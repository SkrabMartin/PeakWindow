"""Model selection on validation, then a single final evaluation on the holdout.

Protocol (pre-registered in docs/methodology.md before the holdout was opened):

1. ``select_model``: fit every Ridge candidate (every feature spec with every alpha) on the
   training origins and score it on the validation origins. The candidate with the
   lowest validation MAE is chosen. Ties go to the earlier spec, then to the larger
   alpha (the simpler model).
2. ``final_evaluation``: refit the chosen configuration on training + validation
   origins, then score it together with both seasonal naive baselines on the
   holdout. The calibration period is never used here, so it stays out-of-sample
   for interval calibration in Phase 4. Results are reported for the whole holdout
   and, as a pre-declared sensitivity view, for origins 2020-04-01 … 2020-06-30
   (the COVID-19 demand drop) and for the remaining holdout origins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from peakwindow.evaluation import (
    ACTUAL,
    EvaluationResult,
    Model,
    ModelMetrics,
    baseline_models,
    build_dataset,
    evaluate_dataset,
    score_models,
)
from peakwindow.models import SPECS, RidgeForecaster
from peakwindow.splits import Period
from peakwindow.uncertainty import LEVELS, ConformalIntervals, CoverageMetrics, coverage_metrics

ALPHAS = (0.01, 0.1, 1.0, 10.0, 100.0, 1000.0)
RIDGE = "ridge"
SENSITIVITY_WINDOW = ("2020-04-01", "2020-06-30")


@dataclass(frozen=True)
class Candidate:
    spec: str
    alpha: float
    validation: ModelMetrics


@dataclass(frozen=True)
class SelectionResult:
    candidates: tuple[Candidate, ...]
    chosen: Candidate
    validation: EvaluationResult  # baselines and the chosen Ridge on validation
    training_origins: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "fit on train, select by validation MAE",
            "alphas": list(ALPHAS),
            "specs": list(SPECS),
            "training_origins": self.training_origins,
            "candidates": [
                {
                    "spec": c.spec,
                    "alpha": c.alpha,
                    "validation_mae": c.validation.mae,
                    "validation_rmse": c.validation.rmse,
                    "validation_relative_mae": c.validation.relative_mae,
                }
                for c in self.candidates
            ],
            "chosen": {"spec": self.chosen.spec, "alpha": self.chosen.alpha},
            "validation": self.validation.to_dict(),
        }


def _fit(frame: pd.DataFrame, spec: str, alpha: float) -> RidgeForecaster:
    return RidgeForecaster(alpha=alpha, spec=spec).fit(frame, frame[ACTUAL])


def select_model(series: pd.DataFrame) -> SelectionResult:
    train = build_dataset(series, (Period.TRAIN,))
    validation = build_dataset(series, (Period.VALIDATION,))
    candidates = []
    for spec in SPECS:
        for alpha in ALPHAS:
            model = _fit(train.frame, spec, alpha)
            metrics = score_models(validation.frame, {RIDGE: model})[RIDGE]
            candidates.append(Candidate(spec, alpha, metrics))
    chosen = min(
        candidates,
        key=lambda c: (c.validation.mae, SPECS.index(c.spec), -c.alpha),
    )
    models: dict[str, Model] = {
        **baseline_models(),
        RIDGE: _fit(train.frame, chosen.spec, chosen.alpha),
    }
    return SelectionResult(
        candidates=tuple(candidates),
        chosen=chosen,
        validation=evaluate_dataset(validation, Period.VALIDATION, models),
        training_origins=train.origins_used,
    )


@dataclass(frozen=True)
class FinalResult:
    spec: str
    alpha: float
    fit_origins: int
    holdout: EvaluationResult
    sensitivity_window: dict[str, ModelMetrics]
    outside_window: dict[str, ModelMetrics]
    model_export: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        first, last = SENSITIVITY_WINDOW
        return {
            "protocol": "refit chosen configuration on train + validation; score holdout once",
            "chosen": {"spec": self.spec, "alpha": self.alpha},
            "fit_periods": [Period.TRAIN.value, Period.VALIDATION.value],
            "fit_origins": self.fit_origins,
            "holdout": self.holdout.to_dict(),
            "sensitivity": {
                "window_origins": [first, last],
                "inside_window": {k: v.to_dict() for k, v in self.sensitivity_window.items()},
                "outside_window": {k: v.to_dict() for k, v in self.outside_window.items()},
            },
        }


def final_evaluation(series: pd.DataFrame, spec: str, alpha: float) -> FinalResult:
    """Refit on train + validation and score the holdout. Run once, after selection."""
    fit_data = build_dataset(series, (Period.TRAIN, Period.VALIDATION))
    ridge = _fit(fit_data.frame, spec, alpha)
    models: dict[str, Model] = {**baseline_models(), RIDGE: ridge}
    holdout_data = build_dataset(series, (Period.HOLDOUT,), allow_holdout=True)
    holdout = evaluate_dataset(holdout_data, Period.HOLDOUT, models)

    frame = holdout_data.frame
    first, last = (pd.Timestamp(day, tz="UTC") for day in SENSITIVITY_WINDOW)
    inside = frame["origin_utc"].between(first, last)
    return FinalResult(
        spec=spec,
        alpha=alpha,
        fit_origins=fit_data.origins_used,
        holdout=holdout,
        sensitivity_window=score_models(frame.loc[inside], models),
        outside_window=score_models(frame.loc[~inside], models),
        model_export=ridge.export(),
    )


@dataclass(frozen=True)
class IntervalResult:
    spec: str
    alpha: float
    intervals: ConformalIntervals
    calibration_origins: int
    holdout_origins: int
    holdout: dict[float, CoverageMetrics]
    sensitivity_window: dict[float, CoverageMetrics]
    outside_window: dict[float, CoverageMetrics]
    model_export: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        def by_level(metrics: dict[float, CoverageMetrics]) -> dict[str, Any]:
            return {f"{level:.2f}": item.to_dict() for level, item in metrics.items()}

        first, last = SENSITIVITY_WINDOW
        return {
            "protocol": "per-horizon split conformal; residuals of the train+validation "
            "Ridge on the calibration period; coverage measured once on the holdout",
            "chosen": {"spec": self.spec, "alpha": self.alpha},
            "levels": list(self.intervals.half_widths),
            "calibration_origins": self.calibration_origins,
            "calibration_rows_per_horizon": list(self.intervals.calibration_rows_per_horizon),
            "half_widths_mw": {
                f"{level:.2f}": list(widths) for level, widths in self.intervals.half_widths.items()
            },
            "holdout_origins": self.holdout_origins,
            "holdout": by_level(self.holdout),
            "sensitivity": {
                "window_origins": [first, last],
                "inside_window": by_level(self.sensitivity_window),
                "outside_window": by_level(self.outside_window),
            },
        }


def interval_evaluation(
    series: pd.DataFrame, spec: str, alpha: float, levels: tuple[float, ...] = LEVELS
) -> IntervalResult:
    """Calibrate intervals on the calibration period and measure coverage on the holdout."""
    fit_data = build_dataset(series, (Period.TRAIN, Period.VALIDATION))
    ridge = _fit(fit_data.frame, spec, alpha)

    calibration = build_dataset(series, (Period.CALIBRATION,)).frame
    intervals = ConformalIntervals.calibrate(
        calibration["horizon_h"].to_numpy(),
        calibration[ACTUAL].to_numpy(),
        ridge.predict(calibration),
        levels,
    )

    holdout = build_dataset(series, (Period.HOLDOUT,), allow_holdout=True).frame
    first, last = (pd.Timestamp(day, tz="UTC") for day in SENSITIVITY_WINDOW)
    inside = holdout["origin_utc"].between(first, last)

    def measure(rows: pd.Series) -> dict[float, CoverageMetrics]:
        part = holdout.loc[rows]
        predicted = ridge.predict(part)
        actual = part[ACTUAL].to_numpy()
        return {
            level: coverage_metrics(part, actual, predicted, intervals, level) for level in levels
        }

    everything = pd.Series(True, index=holdout.index)
    return IntervalResult(
        spec=spec,
        alpha=alpha,
        intervals=intervals,
        calibration_origins=int(calibration["origin_utc"].nunique()),
        holdout_origins=int(holdout["origin_utc"].nunique()),
        holdout=measure(everything),
        sensitivity_window=measure(inside),
        outside_window=measure(~inside),
        model_export=ridge.export(),
    )
