"""Dashboard data helpers and a headless smoke test of the Streamlit app."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from peakwindow.dashboard_data import (
    COLUMNS,
    MODELS,
    daily_mae,
    day_view,
    interval_coverage,
    load_bundle,
    local_day_irregular,
    mae_by,
    mae_by_day_type,
    write_forecasts,
)
from peakwindow.report import build_report

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"


def synthetic_table() -> pd.DataFrame:
    """Small forecast table: two backtest days (one is the autumn DST day) and two holdout days."""
    frames = []
    for source, day, bounds in [
        ("backtest_2019", "2019-10-26", False),
        ("backtest_2019", "2019-10-27", False),
        ("holdout", "2020-01-01", True),
        ("holdout", "2020-01-03", True),  # 2020-01-02 is a data-gap exception
    ]:
        origin = pd.Timestamp(day, tz="UTC")
        targets = pd.date_range(origin, periods=24, freq="h")
        actual = 7000 + 100 * np.arange(24)
        frame = pd.DataFrame(
            {
                "source": source,
                "origin_utc": origin,
                "target_utc": targets,
                "horizon_h": np.arange(24),
                "local_weekday": targets.tz_convert("Europe/Prague").day_of_week,
                "is_weekend": targets.tz_convert("Europe/Prague").day_of_week >= 5,
                "is_holiday": day == "2020-01-01",
                "actual": actual.astype(float),
                "seasonal_naive_daily": actual + 300.0,
                "seasonal_naive_weekly": actual - 200.0,
                "ridge": actual + 50.0,
            }
        )
        for level, width in ((80, 40.0), (90, 100.0)):
            frame[f"ridge_lower_{level}"] = frame["ridge"] - width if bounds else np.nan
            frame[f"ridge_upper_{level}"] = frame["ridge"] + width if bounds else np.nan
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).loc[:, list(COLUMNS)]


def test_day_view_keeps_the_repeated_autumn_hour_distinct() -> None:
    view = day_view(synthetic_table(), pd.Timestamp("2019-10-27", tz="UTC"))

    labels = view["local_label"].tolist()
    assert len(view) == 24
    assert labels[:2] == ["Sun 02:00 +02:00", "Sun 02:00 +01:00"]
    assert len(set(labels)) == 24
    assert "fall back" in str(local_day_irregular(view))


def test_irregular_day_messages() -> None:
    table = synthetic_table()
    assert local_day_irregular(day_view(table, pd.Timestamp("2019-10-26", tz="UTC"))) is None

    spring = table.loc[table["origin_utc"] == pd.Timestamp("2019-10-26", tz="UTC")].copy()
    spring_origin = pd.Timestamp("2019-03-31", tz="UTC")
    spring["origin_utc"] = spring_origin
    spring["target_utc"] = pd.date_range(spring_origin, periods=24, freq="h")
    message = local_day_irregular(day_view(spring, spring_origin))
    assert message is not None
    assert "spring forward" in message


def test_error_summaries() -> None:
    table = synthetic_table()

    by_type = mae_by_day_type(table).set_index("day_type")
    assert by_type.loc["Public holiday", "ridge"] == 50.0
    assert by_type.loc["Weekday", "seasonal_naive_daily"] == 300.0
    assert mae_by(table, "horizon_h")["seasonal_naive_weekly"].eq(200.0).all()
    daily = daily_mae(table)
    assert len(daily) == 4
    assert daily["ridge"].eq(50.0).all()


def test_interval_coverage_uses_only_rows_with_bounds() -> None:
    coverage = interval_coverage(synthetic_table()).set_index("nominal")

    # The actual value is 50 MW below Ridge: outside ±40, inside ±100.
    assert coverage.loc["80 %", "coverage"] == 0.0
    assert coverage.loc["90 %", "coverage"] == 1.0
    assert coverage.loc["90 %", "mean_width_mw"] == 200.0


def test_bundle_roundtrip(tmp_path: Path) -> None:
    table = synthetic_table()
    write_forecasts(table, tmp_path)
    (tmp_path / "backtest.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    forecasts, records = load_bundle(tmp_path)

    pd.testing.assert_frame_equal(forecasts, table)
    assert set(records) == {"backtest"}


def test_report_without_records_says_so() -> None:
    report = build_report({})
    assert report.startswith("# PeakWindow experiment report")
    assert "Not a live forecast" in report
    assert report.count("Not available.") == 4
    assert "holdout: 2020-01-01 … 2020-09-29" in report


def test_dashboard_app_renders_without_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    write_forecasts(synthetic_table(), tmp_path)
    monkeypatch.setenv("PEAKWINDOW_EXPERIMENTS", str(tmp_path))
    app = AppTest.from_file(str(APP), default_timeout=60)

    app.run()
    assert not app.exception
    assert "Historical reconstruction, not a live forecast" in app.warning[0].value
    assert app.metric[0].label == "Ridge: daily MAE"

    app.date_input[0].set_value(dt.date(2020, 1, 2)).run()  # data-gap exception
    assert not app.exception
    assert "data-gap exception" in app.info[0].value
    assert app.subheader  # the other tabs still render

    app.selectbox[0].set_value("backtest_2019").run()
    app.date_input[0].set_value(dt.date(2019, 10, 27)).run()
    assert not app.exception
    assert any("fall back" in caption.value for caption in app.caption)


def test_dashboard_app_explains_missing_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("PEAKWINDOW_EXPERIMENTS", str(tmp_path / "missing"))
    app = AppTest.from_file(str(APP), default_timeout=60)
    app.run()

    assert not app.exception
    assert "export-dashboard" in app.error[0].value


@pytest.mark.parametrize("model", MODELS)
def test_models_have_labels(model: str) -> None:
    from peakwindow.dashboard_data import MODEL_LABELS

    assert MODEL_LABELS[model]
