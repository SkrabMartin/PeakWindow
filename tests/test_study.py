"""Model selection and final holdout protocol, on a full-length synthetic series."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from peakwindow import cli
from peakwindow.backtest import BACKTEST_YEARS, BacktestResult, rolling_backtest
from peakwindow.cli import EXIT_OK, EXIT_USAGE_OR_IO_ERROR, main
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN, ForecastRequest
from peakwindow.features import MAX_LOOKBACK, build_features
from peakwindow.study import (
    ALPHAS,
    SPECS,
    FinalResult,
    IntervalResult,
    SelectionResult,
    final_evaluation,
    interval_evaluation,
    select_model,
)

from conftest import hourly_series, write_processed


def synthetic_load(i: int) -> float:
    """Daily cycle, weekly cycle, slow drift and a fixed ripple (no randomness)."""
    hour, day = i % 24, (i // 24) % 7
    return (
        7000
        + 1200 * math.sin(2 * math.pi * (hour - 6) / 24)
        - (800 if day >= 5 else 0)
        + 300 * math.sin(2 * math.pi * i / (24 * 365))
        + ((i * 37) % 11 - 5) * 20
    )


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    return hourly_series("2015-01-01T01:00:00Z", "2020-09-30T22:00:00Z", synthetic_load)


@pytest.fixture(scope="module")
def selection(series: pd.DataFrame) -> SelectionResult:
    return select_model(series)


@pytest.fixture(scope="module")
def final(series: pd.DataFrame, selection: SelectionResult) -> FinalResult:
    return final_evaluation(series, selection.chosen.spec, selection.chosen.alpha)


@pytest.fixture(scope="module")
def backtest(series: pd.DataFrame, selection: SelectionResult) -> BacktestResult:
    return rolling_backtest(series, selection.chosen.spec, selection.chosen.alpha)


@pytest.fixture(scope="module")
def intervals(series: pd.DataFrame, selection: SelectionResult) -> IntervalResult:
    return interval_evaluation(series, selection.chosen.spec, selection.chosen.alpha)


def test_selection_fits_every_candidate_and_picks_lowest_validation_mae(
    selection: SelectionResult,
) -> None:
    assert len(selection.candidates) == len(SPECS) * len(ALPHAS)
    best = min(c.validation.mae for c in selection.candidates)
    assert selection.chosen.validation.mae == best
    assert selection.validation.period == "validation"
    assert set(selection.validation.models) == {
        "seasonal_naive_daily",
        "seasonal_naive_weekly",
        "ridge",
    }
    assert selection.validation.models["ridge"].mae == selection.chosen.validation.mae


def test_selection_cannot_see_data_after_validation(
    series: pd.DataFrame, selection: SelectionResult
) -> None:
    """Destroying calibration and holdout data must not change the selection at all."""
    damaged = series.copy()
    after = damaged[TIMESTAMP_COLUMN] >= pd.Timestamp("2019-07-01T00:00:00Z")
    damaged.loc[after, LOAD_COLUMN] = -1.0e6

    again = select_model(damaged)

    assert again.chosen == selection.chosen
    assert again.candidates == selection.candidates


def test_window_features_equal_full_series_features(series: pd.DataFrame) -> None:
    for origin in pd.date_range("2015-01-09", "2020-09-29", freq="97D", tz="UTC"):
        request = ForecastRequest(origin_utc=origin)
        stamps = series[TIMESTAMP_COLUMN]
        window = series[(stamps >= origin - MAX_LOOKBACK) & (stamps < origin)]
        pd.testing.assert_frame_equal(
            build_features(window, request), build_features(series, request)
        )


def test_final_evaluation_refits_on_train_and_validation(
    selection: SelectionResult, final: FinalResult
) -> None:
    result = final

    fit_origins = selection.training_origins + selection.validation.origins_evaluated
    assert result.fit_origins == fit_origins
    assert result.holdout.period == "holdout"
    assert result.holdout.origins_evaluated == 273
    assert result.holdout.target_hours_evaluated == 273 * 24
    assert len(result.model_export["horizons"]) == 24
    inside = result.sensitivity_window["ridge"]
    outside = result.outside_window["ridge"]
    assert inside.mae > 0
    assert outside.mae > 0
    record = result.to_dict()
    assert record["fit_periods"] == ["train", "validation"]
    assert record["sensitivity"]["window_origins"] == ["2020-04-01", "2020-06-30"]


def test_cli_selection_then_final_holdout(
    series: pd.DataFrame,
    selection: SelectionResult,
    final: FinalResult,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI wiring and records; the computations themselves are tested above."""
    calls: list[tuple[str, float]] = []

    def fake_final(_: pd.DataFrame, spec: str, alpha: float) -> FinalResult:
        calls.append((spec, alpha))
        return final

    monkeypatch.setattr(cli, "select_model", lambda _: selection)
    monkeypatch.setattr(cli, "final_evaluation", fake_final)
    data = tmp_path / "data"
    write_processed(series, data / "processed")
    common = ["--data-dir", str(data), "--output-dir", str(tmp_path / "exp")]

    assert main(["evaluate-holdout", *common, "--confirm"]) == EXIT_USAGE_OR_IO_ERROR
    assert "run select-model first" in capsys.readouterr().err
    assert main(["evaluate-holdout", *common]) == EXIT_USAGE_OR_IO_ERROR
    assert "--confirm" in capsys.readouterr().err

    assert main(["select-model", *common]) == EXIT_OK
    assert "chosen spec=" in capsys.readouterr().err
    assert main(["evaluate-holdout", *common, "--confirm"]) == EXIT_OK
    assert "holdout 273 of 273 origins" in capsys.readouterr().err

    record = json.loads((tmp_path / "exp" / "final_holdout.json").read_text(encoding="utf-8"))
    selected = json.loads((tmp_path / "exp" / "model_selection.json").read_text(encoding="utf-8"))
    # The final run used exactly the configuration recorded by select-model.
    assert calls == [(selection.chosen.spec, selection.chosen.alpha)]
    assert record["result"]["chosen"] == selected["selection"]["chosen"]
    assert record["model_artifact"]["file_name"] == "ridge_final_model.json"
    assert len(record["model_artifact"]["sha256"]) == 64


def test_cli_refuses_selection_made_on_other_data(
    series: pd.DataFrame, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    write_processed(series, data / "processed")
    experiments = tmp_path / "exp"
    experiments.mkdir()
    (experiments / "model_selection.json").write_text(
        json.dumps({"data": {"processed_sha256": "0" * 64}, "selection": {}}), encoding="utf-8"
    )

    args = ["evaluate-holdout", "--data-dir", str(data), "--output-dir", str(experiments)]
    assert main([*args, "--confirm"]) == EXIT_USAGE_OR_IO_ERROR
    assert "different data" in capsys.readouterr().err


# --- Phase 4: backtest and intervals ----------------------------------------


def test_backtest_folds_fit_only_on_the_past(backtest: BacktestResult) -> None:
    assert [fold.test_year for fold in backtest.folds] == list(BACKTEST_YEARS)
    fit_sizes = [fold.fit_origins for fold in backtest.folds]
    assert fit_sizes == sorted(fit_sizes)  # expanding window
    for fold in backtest.folds:
        assert fold.fit_last_origin_utc < f"{fold.test_year}-01-01"
        assert fold.fit_first_origin_utc == "2015-01-09T00:00:00Z"
        assert set(fold.models) == {"seasonal_naive_daily", "seasonal_naive_weekly", "ridge"}


def test_backtest_never_reads_the_holdout(
    series: pd.DataFrame, selection: SelectionResult, backtest: BacktestResult
) -> None:
    damaged = series.copy()
    holdout = damaged[TIMESTAMP_COLUMN] >= pd.Timestamp("2020-01-01T00:00:00Z")
    damaged.loc[holdout, LOAD_COLUMN] = -1.0e6

    again = rolling_backtest(damaged, selection.chosen.spec, selection.chosen.alpha)

    assert again == backtest


def test_intervals_use_the_final_model_and_calibration_period(
    intervals: IntervalResult, final: FinalResult
) -> None:
    # Same deterministic refit as the final point evaluation.
    assert intervals.model_export == final.model_export
    assert intervals.calibration_origins == 184
    assert intervals.intervals.calibration_rows_per_horizon == (184,) * 24
    assert intervals.holdout_origins == 273
    widths_80 = intervals.intervals.half_widths[0.8]
    widths_90 = intervals.intervals.half_widths[0.9]
    assert all(w90 >= w80 > 0 for w80, w90 in zip(widths_80, widths_90, strict=True))
    for level, metrics in intervals.holdout.items():
        assert 0 <= metrics.coverage <= 1
        assert metrics.level == level
    record = intervals.to_dict()
    assert set(record["holdout"]) == {"0.80", "0.90"}
    assert len(record["half_widths_mw"]["0.90"]) == 24


def test_cli_backtest_and_intervals(
    series: pd.DataFrame,
    selection: SelectionResult,
    backtest: BacktestResult,
    intervals: IntervalResult,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "select_model", lambda _: selection)
    monkeypatch.setattr(cli, "rolling_backtest", lambda *_: backtest)
    monkeypatch.setattr(cli, "interval_evaluation", lambda *_: intervals)
    data = tmp_path / "data"
    write_processed(series, data / "processed")
    experiments = tmp_path / "exp"
    common = ["--data-dir", str(data), "--output-dir", str(experiments)]

    assert main(["backtest", *common]) == EXIT_USAGE_OR_IO_ERROR
    assert "run select-model first" in capsys.readouterr().err
    assert main(["select-model", *common]) == EXIT_OK
    assert main(["backtest", *common]) == EXIT_OK
    assert "2019 (fit on" in capsys.readouterr().err

    assert main(["evaluate-intervals", *common]) == EXIT_USAGE_OR_IO_ERROR
    assert "--confirm" in capsys.readouterr().err
    assert main(["evaluate-intervals", *common, "--confirm"]) == EXIT_OK
    assert "90% interval: coverage" in capsys.readouterr().err
    record = json.loads((experiments / "intervals_holdout.json").read_text(encoding="utf-8"))
    assert record["model_matches_final_holdout_model"] is False  # no final model stored

    # A stored final model that differs from the refit is refused.
    (experiments / "ridge_final_model.json").write_text('{"alpha": -1}', encoding="utf-8")
    assert main(["evaluate-intervals", *common, "--confirm"]) == EXIT_USAGE_OR_IO_ERROR
    assert "differs" in capsys.readouterr().err
    (experiments / "ridge_final_model.json").write_text(
        json.dumps(intervals.model_export), encoding="utf-8"
    )
    assert main(["evaluate-intervals", *common, "--confirm"]) == EXIT_OK
