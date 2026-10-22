"""Rolling-origin backtest with yearly refits and an expanding window.

For each test year ``Y`` in 2016-2019, the selected Ridge configuration is refitted
on every usable origin *before* 1 January ``Y`` and scored, together with both
seasonal naive baselines, on the origins of year ``Y``. The holdout (2020) is never
used.

Caveat: the configuration (feature spec and alpha) was chosen on the 2019 H1
validation period, so for the 2016-2018 folds the configuration is informed by
later data. Only the coefficients are strictly refitted on the past. With two specs
and six alphas, this choice carries little freedom.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from peakwindow.evaluation import (
    ACTUAL,
    Model,
    ModelMetrics,
    baseline_models,
    build_dataset,
    score_models,
)
from peakwindow.models import RidgeForecaster
from peakwindow.splits import Period

BACKTEST_YEARS = (2016, 2017, 2018, 2019)
RIDGE = "ridge"


@dataclass(frozen=True)
class Fold:
    test_year: int
    fit_first_origin_utc: str
    fit_last_origin_utc: str
    fit_origins: int
    test_origins: int
    models: dict[str, ModelMetrics]

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_year": self.test_year,
            "fit_first_origin_utc": self.fit_first_origin_utc,
            "fit_last_origin_utc": self.fit_last_origin_utc,
            "fit_origins": self.fit_origins,
            "test_origins": self.test_origins,
            "models": {name: metrics.to_dict() for name, metrics in self.models.items()},
        }


@dataclass(frozen=True)
class BacktestResult:
    spec: str
    alpha: float
    folds: tuple[Fold, ...]
    excluded_origins: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": "expanding window, yearly refit, holdout excluded",
            "chosen": {"spec": self.spec, "alpha": self.alpha},
            "excluded_origins": self.excluded_origins,
            "folds": [fold.to_dict() for fold in self.folds],
        }


def _format_day(origin: pd.Timestamp) -> str:
    return origin.strftime("%Y-%m-%dT%H:%M:%SZ")


def rolling_backtest(
    series: pd.DataFrame, spec: str, alpha: float, years: tuple[int, ...] = BACKTEST_YEARS
) -> BacktestResult:
    dataset = build_dataset(series, (Period.TRAIN, Period.VALIDATION, Period.CALIBRATION))
    frame = dataset.frame
    origin_year = frame["origin_utc"].dt.year
    folds = []
    for year in years:
        fit = frame.loc[origin_year < year]
        test = frame.loc[origin_year == year]
        if fit.empty or test.empty:
            raise ValueError(f"backtest year {year} has no fit or test origins")
        ridge = RidgeForecaster(alpha=alpha, spec=spec).fit(fit, fit[ACTUAL])
        models: dict[str, Model] = {**baseline_models(), RIDGE: ridge}
        folds.append(
            Fold(
                test_year=year,
                fit_first_origin_utc=_format_day(fit["origin_utc"].min()),
                fit_last_origin_utc=_format_day(fit["origin_utc"].max()),
                fit_origins=int(fit["origin_utc"].nunique()),
                test_origins=int(test["origin_utc"].nunique()),
                models=score_models(test, models),
            )
        )
    return BacktestResult(spec, alpha, tuple(folds), len(dataset.exceptions))
