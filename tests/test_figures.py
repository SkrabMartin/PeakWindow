"""Report figures: valid, deterministic SVG drawn from the records only."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import numpy as np
import pandas as pd

from peakwindow.dashboard_data import COLUMNS, MODELS
from peakwindow.figures import FIGURE_ALT, build_figures, example_week
from peakwindow.report import build_report

SVG = "{http://www.w3.org/2000/svg}"


def metrics(mae: float) -> dict[str, Any]:
    return {"mae": mae, "mae_by_horizon": [mae + h for h in range(24)]}


def records() -> dict[str, dict[str, Any]]:
    models = {m: metrics(100.0 * (i + 1)) for i, m in enumerate(MODELS)}
    coverage = {
        level: {"coverage_by_horizon": [float(level) - 0.01 * (h % 3) for h in range(24)]}
        for level in ("0.80", "0.90")
    }
    folds = [
        {"test_year": year, "fit_origins": 365, "models": models}
        for year in (2016, 2017, 2018, 2019)
    ]
    return {
        "final_holdout": {"result": {"holdout": {"models": models}}},
        "intervals_holdout": {"result": {"holdout": coverage}},
        "backtest": {"result": {"folds": folds}},
    }


def polylines(svg: str) -> list[ET.Element]:
    return list(ET.fromstring(svg).iter(f"{SVG}polyline"))


def week_table(first: str = "2020-01-06", days: int = 8) -> pd.DataFrame:
    """Consecutive holdout days; the default starts on Monday 2020-01-06."""
    frames = []
    for i in range(days):
        origin = pd.Timestamp(first, tz="UTC") + pd.Timedelta(days=i)
        actual = 7000.0 + 100 * np.arange(24)
        frame = pd.DataFrame(
            {
                "source": "holdout",
                "origin_utc": origin,
                "target_utc": pd.date_range(origin, periods=24, freq="h"),
                "horizon_h": np.arange(24),
                "local_weekday": origin.day_of_week,
                "is_weekend": False,
                "is_holiday": False,
                "actual": actual,
                "seasonal_naive_daily": actual + 300,
                "seasonal_naive_weekly": actual - 200,
                "ridge": actual + 50,
            }
        )
        for level, width in ((80, 40.0), (90, 100.0)):
            frame[f"ridge_lower_{level}"] = frame["ridge"] - width
            frame[f"ridge_upper_{level}"] = frame["ridge"] + width
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).loc[:, list(COLUMNS)]


def test_figures_are_valid_svg_with_one_line_per_series() -> None:
    figures = build_figures(records())

    assert set(figures) == set(FIGURE_ALT) - {"holdout_example_week.svg"}
    assert len(polylines(figures["holdout_mae_by_horizon.svg"])) == 3
    assert len(polylines(figures["interval_coverage_by_horizon.svg"])) == 4  # 2 levels x 2
    backtest = ET.fromstring(figures["backtest_mae_by_year.svg"])
    assert len(list(backtest.iter(f"{SVG}circle"))) == 3 * 4
    for svg in figures.values():
        root = ET.fromstring(svg)
        assert root.find(f"{SVG}title") is not None
        assert all(len(line.get("points", "").split()) >= 4 for line in polylines(svg))


def test_figures_are_deterministic_and_follow_the_data() -> None:
    assert build_figures(records()) == build_figures(records())

    changed = records()
    changed["final_holdout"]["result"]["holdout"]["models"]["ridge"] = metrics(50.0)
    assert (
        build_figures(changed)["holdout_mae_by_horizon.svg"]
        != build_figures(records())["holdout_mae_by_horizon.svg"]
    )


def test_figures_skip_missing_records() -> None:
    only_backtest = {"backtest": records()["backtest"]}
    assert set(build_figures(only_backtest)) == {"backtest_mae_by_year.svg"}
    assert build_figures({}) == {}


def test_example_week_uses_the_first_complete_monday_week() -> None:
    svg = example_week(week_table())

    assert svg is not None
    assert "2020-01-06" in svg
    root = ET.fromstring(svg)
    assert len(list(root.iter(f"{SVG}polygon"))) == 1  # the 90 % band
    assert all(len(line.get("points", "").split()) == 7 * 24 for line in polylines(svg))


def test_example_week_needs_seven_consecutive_origins() -> None:
    table = week_table()
    gap = table["origin_utc"] == pd.Timestamp("2020-01-08", tz="UTC")
    assert example_week(table.loc[~gap]) is None
    assert example_week(week_table("2020-01-07", days=7)) is None  # starts on a Tuesday


def test_report_embeds_only_written_figures() -> None:
    only_backtest = {"backtest": records()["backtest"]}
    report = build_report(only_backtest, ["backtest_mae_by_year.svg", "unused.svg"])

    assert (
        "![Rolling-backtest MAE by test year for each model](figures/backtest_mae_by_year.svg)"
        in report
    )
    assert "unused.svg" not in report
    assert "figures/" not in build_report(only_backtest)
