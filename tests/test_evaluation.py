"""Frozen split, baseline evaluation, data-gap exceptions and the holdout lock."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

from peakwindow import baselines
from peakwindow.cli import EXIT_OK, EXIT_USAGE_OR_IO_ERROR, main
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN, ContractError
from peakwindow.evaluation import HoldoutLockedError, evaluate_baselines
from peakwindow.prepare import PreparationError, load_processed, output_paths
from peakwindow.splits import SPLIT_V1, Period, origins_for, parse_period

from conftest import hourly_series, write_processed


def linear_series(
    start: str = "2018-12-20T00:00:00Z", end: str = "2019-07-01T23:00:00Z"
) -> pd.DataFrame:
    """load = 1000 + i: daily naive is always 24 MW too low, weekly 168 MW too low."""
    return hourly_series(start, end, lambda i: 1000 + i)


# --- split ------------------------------------------------------------------


def test_split_periods_are_contiguous_and_ordered() -> None:
    periods = [Period.TRAIN, Period.VALIDATION, Period.CALIBRATION, Period.HOLDOUT]
    origins = [origins_for(period) for period in periods]

    for earlier, later in pairwise(origins):
        assert later[0] - earlier[-1] == pd.Timedelta(days=1)
    assert [len(o) for o in origins] == [1453, 181, 184, 273]
    for period_origins in origins:
        assert (period_origins == period_origins.normalize()).all()
        assert str(period_origins.tz) == "UTC"


def test_split_matches_audited_coverage() -> None:
    # First value 2015-01-01T01Z: the weekly lag of origin 2015-01-09 at h=0 is 2015-01-02T00Z.
    assert SPLIT_V1[Period.TRAIN].first_origin == "2015-01-09"
    # Last value 2020-09-30T22Z: origin 2020-09-29 is the last with all 24 targets.
    assert SPLIT_V1[Period.HOLDOUT].last_origin == "2020-09-29"


def test_unknown_period_is_rejected() -> None:
    with pytest.raises(ContractError, match="unknown period"):
        parse_period("test")
    assert parse_period("validation") is Period.VALIDATION


# --- evaluation -------------------------------------------------------------


def test_metrics_on_a_series_with_known_errors() -> None:
    series = linear_series()

    result = evaluate_baselines(series, Period.VALIDATION)

    assert result.origins_in_period == result.origins_evaluated == 181
    assert result.target_hours_evaluated == 181 * 24
    assert result.exceptions == ()
    daily = result.models["seasonal_naive_daily"]
    weekly = result.models["seasonal_naive_weekly"]
    assert daily.mae == daily.rmse == 24.0
    assert weekly.mae == weekly.rmse == 168.0
    assert weekly.relative_mae == 1.0
    assert daily.relative_mae == round(24 / 168, 3)
    assert daily.mae_by_horizon == (24.0,) * 24
    assert daily.mae_weekday == daily.mae_weekend == 24.0
    # nMAE uses the mean actual load over the evaluated targets.
    by_time = series.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    mean_actual = by_time["2019-01-01T00:00Z":"2019-06-30T23:00Z"].mean()
    assert daily.nmae_pct == round(100 * 24 / mean_actual, 3)


def test_missing_hour_creates_target_and_input_exceptions() -> None:
    series = linear_series()
    gap = pd.Timestamp("2019-03-05T10:00:00Z")
    series.loc[series[TIMESTAMP_COLUMN] == gap, LOAD_COLUMN] = float("nan")

    result = evaluate_baselines(series, Period.VALIDATION)

    reasons = {item.origin_utc[:10]: item.reasons for item in result.exceptions}
    # Its own day loses a target; the next day needs it as lag_24h and in prev_24h;
    # exactly one week later needs it as lag_168h. Days in between are unaffected.
    assert reasons == {
        "2019-03-05": ("missing_target",),
        "2019-03-06": ("missing_input",),
        "2019-03-12": ("missing_input",),
    }
    assert result.origins_evaluated == 181 - 3
    # Every model is scored on the same remaining origins, so the errors stay exact.
    assert result.models["seasonal_naive_daily"].mae == 24.0


def test_holdout_is_locked_by_default() -> None:
    with pytest.raises(HoldoutLockedError, match="locked"):
        evaluate_baselines(linear_series(), Period.HOLDOUT)


def test_holdout_can_be_unlocked_explicitly() -> None:
    series = linear_series("2019-12-20T00:00:00Z", "2020-09-30T23:00:00Z")

    result = evaluate_baselines(series, Period.HOLDOUT, allow_holdout=True)

    assert result.origins_evaluated == 273


def test_period_without_evaluable_origins_fails() -> None:
    ends_before_calibration = linear_series(end="2019-06-30T23:00:00Z")
    with pytest.raises(ValueError, match="no evaluable origins"):
        evaluate_baselines(ends_before_calibration, Period.CALIBRATION)


def test_unknown_baseline_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown baseline"):
        baselines.predict(pd.DataFrame(), "prophet")


# --- processed data and CLI -------------------------------------------------


def test_load_processed_verifies_checksum(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    parquet = write_processed(linear_series(), processed)

    series, digest = load_processed(processed)
    assert len(series) == len(linear_series())
    assert len(digest) == 64

    parquet.write_bytes(parquet.read_bytes() + b"x")
    with pytest.raises(PreparationError, match="does not match the SHA-256"):
        load_processed(processed)


def test_load_processed_requires_prepare(tmp_path: Path) -> None:
    with pytest.raises(PreparationError, match="prepare-opsd"):
        load_processed(tmp_path / "processed")
    _, record = output_paths(tmp_path / "other")
    record.parent.mkdir()
    record.write_text(json.dumps({"processed": None}), encoding="utf-8")
    with pytest.raises(PreparationError, match="no processed series"):
        load_processed(tmp_path / "other")


def test_cli_evaluate_writes_experiment_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_processed(linear_series(), tmp_path / "data" / "processed")
    args = ["evaluate-baselines", "--period", "validation", "--data-dir", str(tmp_path / "data")]

    assert main([*args, "--output-dir", str(tmp_path / "exp")]) == EXIT_OK

    err = capsys.readouterr().err
    assert "181 of 181 origins evaluated" in err
    assert "seasonal_naive_weekly: MAE 168.0 MW" in err
    record = json.loads((tmp_path / "exp" / "baselines_validation.json").read_text("utf-8"))
    assert record["evaluation"]["split_version"] == "split-v1"
    assert record["config"]["spike_policy"].startswith("scored as published")
    assert len(record["data"]["processed_sha256"]) == 64
    assert record["evaluation"]["models"]["seasonal_naive_daily"]["mae"] == 24.0


def test_cli_rejects_holdout_and_missing_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = ["--data-dir", str(tmp_path / "data")]
    assert main(["evaluate-baselines", "--period", "holdout", *data]) == EXIT_USAGE_OR_IO_ERROR
    assert "invalid choice: 'holdout'" in capsys.readouterr().err

    assert main(["evaluate-baselines", "--period", "train", *data]) == EXIT_USAGE_OR_IO_ERROR
    assert "prepare-opsd" in capsys.readouterr().err
