"""Precomputed data for the historical dashboard, plus the summaries it displays.

``export-dashboard`` builds one table of out-of-sample forecasts and writes it to
``<experiments>/dashboard/forecasts.parquet``:

* ``backtest_<year>`` rows: 2016-2019, Ridge refitted on earlier years only
* ``holdout`` rows: 2020, the final Ridge with its 80 % / 90 % conformal bounds

The dashboard only reads this file and the JSON experiment records; it never fits
a model. Everything here is pure except the two file helpers at the end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from peakwindow.backtest import backtest_predictions
from peakwindow.evaluation import ACTUAL
from peakwindow.study import RIDGE, holdout_predictions

PRAGUE = ZoneInfo("Europe/Prague")
MODELS = ("seasonal_naive_daily", "seasonal_naive_weekly", RIDGE)
MODEL_LABELS = {
    "seasonal_naive_daily": "Seasonal naive (yesterday)",
    "seasonal_naive_weekly": "Seasonal naive (last week)",
    RIDGE: "Ridge",
}
BOUNDS = ("ridge_lower_80", "ridge_upper_80", "ridge_lower_90", "ridge_upper_90")
COLUMNS = (
    "source",
    "origin_utc",
    "target_utc",
    "horizon_h",
    "local_weekday",
    "is_weekend",
    "is_holiday",
    ACTUAL,
    *MODELS,
    *BOUNDS,
)
FORECASTS_FILE = Path("dashboard") / "forecasts.parquet"
RECORDS = (
    "model_selection",
    "final_holdout",
    "intervals_holdout",
    "backtest",
    "baselines_train",
    "baselines_validation",
)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def build_forecast_table(series: pd.DataFrame, spec: str, alpha: float) -> pd.DataFrame:
    """Backtest and holdout forecasts in one table (bounds are NaN outside the holdout)."""
    table = pd.concat(
        [backtest_predictions(series, spec, alpha), holdout_predictions(series, spec, alpha)],
        ignore_index=True,
    )
    for column in BOUNDS:
        if column not in table:
            table[column] = float("nan")
    return table.loc[:, list(COLUMNS)].sort_values(["origin_utc", "horizon_h"], ignore_index=True)


def day_view(table: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    """The 24 rows of one origin, with Prague local labels for display.

    ``local_label`` is "HH:MM" plus the UTC offset, so the repeated hour of the
    autumn change and the missing hour of the spring change stay distinguishable.
    """
    rows = table.loc[table["origin_utc"] == origin].sort_values("horizon_h")
    local = rows["target_utc"].dt.tz_convert(PRAGUE)
    offsets = local.map(lambda t: t.strftime("%z")).str.replace(
        r"(\d{2})(\d{2})$", r"\1:\2", regex=True
    )
    return rows.assign(
        target_local=local.dt.tz_localize(None),
        local_label=local.dt.strftime("%a %H:%M ") + offsets,
    ).reset_index(drop=True)


def local_day_irregular(view: pd.DataFrame) -> str | None:
    """Describe a daylight-saving change inside the 24 target hours, if any."""
    offsets = view["local_label"].str[-6:].unique()
    if len(offsets) < 2:
        return None
    first, last = offsets[0], offsets[-1]
    change = (
        "spring forward (one local hour skipped)"
        if first < last
        else ("fall back (one local hour repeated)")
    )
    return f"Daylight-saving change within this window: {first} → {last}, {change}."


def absolute_errors(table: pd.DataFrame) -> pd.DataFrame:
    """One column per model with the absolute error in MW."""
    return pd.DataFrame({name: (table[name] - table[ACTUAL]).abs() for name in MODELS})


def daily_mae(table: pd.DataFrame) -> pd.DataFrame:
    """MAE per origin and model, with the source and day type of each origin."""
    errors = absolute_errors(table).assign(origin_utc=table["origin_utc"])
    per_day = errors.groupby("origin_utc")[list(MODELS)].mean()
    info = table.groupby("origin_utc")[["source", "is_holiday", "local_weekday"]].first()
    return per_day.join(info).reset_index()


def mae_by(table: pd.DataFrame, column: str) -> pd.DataFrame:
    """MAE per model grouped by ``column`` (for example ``horizon_h``)."""
    errors = absolute_errors(table).assign(**{column: table[column]})
    return errors.groupby(column)[list(MODELS)].mean().reset_index()


def mae_by_day_type(table: pd.DataFrame) -> pd.DataFrame:
    """MAE per model for ordinary weekdays, weekends and public holidays."""
    day_type = pd.Series("Weekday", index=table.index)
    day_type[table["is_weekend"].astype(bool)] = "Weekend"
    day_type[table["is_holiday"].astype(bool)] = "Public holiday"
    errors = absolute_errors(table).assign(day_type=day_type)
    order = ["Weekday", "Weekend", "Public holiday"]
    summary = errors.groupby("day_type")[list(MODELS)].mean().reindex(order).dropna(how="all")
    return summary.reset_index()


def mae_by_source(table: pd.DataFrame) -> pd.DataFrame:
    errors = absolute_errors(table).assign(source=table["source"])
    return errors.groupby("source")[list(MODELS)].mean().reset_index()


def interval_coverage(table: pd.DataFrame) -> pd.DataFrame:
    """Empirical coverage and mean width of the Ridge intervals where bounds exist."""
    rows = []
    for level in (80, 90):
        lower, upper = table[f"ridge_lower_{level}"], table[f"ridge_upper_{level}"]
        has_bounds = lower.notna() & upper.notna()
        if not has_bounds.any():
            continue
        actual = table.loc[has_bounds, ACTUAL]
        inside = (actual >= lower[has_bounds]) & (actual <= upper[has_bounds])
        rows.append(
            {
                "nominal": f"{level} %",
                "coverage": float(inside.mean()),
                "mean_width_mw": float((upper - lower)[has_bounds].mean()),
            }
        )
    return pd.DataFrame(rows, columns=["nominal", "coverage", "mean_width_mw"])


# --- files ------------------------------------------------------------------


def write_forecasts(table: pd.DataFrame, experiments_dir: Path) -> Path:
    path = experiments_dir / FORECASTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(path, index=False)
    return path


def load_bundle(experiments_dir: Path) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Read the forecast table and whichever experiment records exist."""
    forecasts = pd.read_parquet(experiments_dir / FORECASTS_FILE)
    records: dict[str, dict[str, Any]] = {}
    for name in RECORDS:
        path = experiments_dir / f"{name}.json"
        if path.is_file():
            records[name] = json.loads(path.read_text(encoding="utf-8"))
    return forecasts, records
