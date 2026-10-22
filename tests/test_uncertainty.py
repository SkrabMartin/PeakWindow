"""Split-conformal intervals: quantile rule, calibration and coverage metrics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from peakwindow.uncertainty import ConformalIntervals, conformal_quantile, coverage_metrics


def test_conformal_quantile_uses_the_finite_sample_rank() -> None:
    residuals = np.arange(1.0, 10.0)  # n = 9, shuffled order must not matter
    np.random.default_rng(0).shuffle(residuals)

    assert conformal_quantile(residuals, 0.8) == 8.0  # ceil(10 * 0.8) = 8
    assert conformal_quantile(residuals, 0.9) == 9.0  # ceil(10 * 0.9) = 9
    with pytest.raises(ValueError, match="too few"):
        conformal_quantile(residuals, 0.95)  # ceil(10 * 0.95) = 10 > n
    with pytest.raises(ValueError, match="between 0 and 1"):
        conformal_quantile(residuals, 1.0)
    with pytest.raises(ValueError, match="too few"):
        conformal_quantile(np.array([]), 0.5)


def test_half_widths_are_per_horizon() -> None:
    horizons = np.repeat(np.arange(24), 50)
    # Residual scale grows with the horizon: |error| = h + 1 for every row.
    predicted = np.zeros(len(horizons))
    actual = horizons + 1.0

    intervals = ConformalIntervals.calibrate(horizons, actual, predicted)

    assert intervals.half_widths[0.8] == tuple(float(h + 1) for h in range(24))
    assert intervals.calibration_rows_per_horizon == (50,) * 24
    lower, upper = intervals.bounds(np.array([0, 23]), np.array([100.0, 100.0]), 0.9)
    assert lower.tolist() == [99.0, 76.0]
    assert upper.tolist() == [101.0, 124.0]


def frame_for(horizons: np.ndarray) -> pd.DataFrame:
    days = np.arange(len(horizons)) // 24
    return pd.DataFrame(
        {
            "horizon_h": horizons,
            "is_weekend": days % 7 >= 5,
            "is_holiday": days % 50 == 0,
        }
    )


def test_coverage_is_close_to_nominal_on_exchangeable_errors() -> None:
    rng = np.random.default_rng(42)
    calibration_h = np.tile(np.arange(24), 600)
    test_h = np.tile(np.arange(24), 2000)
    scale = 50 + 10 * np.arange(24)
    calibration_error = rng.normal(0, scale[calibration_h])
    test_error = rng.normal(0, scale[test_h])

    intervals = ConformalIntervals.calibrate(
        calibration_h, calibration_error, np.zeros(len(calibration_h))
    )
    for level in (0.8, 0.9):
        metrics = coverage_metrics(
            frame_for(test_h), test_error, np.zeros(len(test_h)), intervals, level
        )
        assert abs(metrics.coverage - level) < 0.01
        assert len(metrics.coverage_by_horizon) == 24
        # Each share is rounded to 4 decimals.
        assert metrics.below_lower + metrics.above_upper == pytest.approx(
            1 - metrics.coverage, abs=2e-4
        )


def test_coverage_metrics_counts_misses_and_width() -> None:
    horizons = np.zeros(4, dtype="int64")
    intervals = ConformalIntervals({0.8: (10.0,) * 24}, (4,) * 24)
    frame = pd.DataFrame(
        {"horizon_h": horizons, "is_weekend": [False] * 4, "is_holiday": [False] * 4}
    )

    metrics = coverage_metrics(
        frame, np.array([0.0, 10.0, -11.0, 25.0]), np.zeros(4), intervals, 0.8
    )

    assert metrics.coverage == 0.5  # 0 and 10 are inside (bounds are inclusive)
    assert metrics.below_lower == 0.25
    assert metrics.above_upper == 0.25
    assert metrics.mean_width_mw == 20.0
    assert metrics.coverage_holiday is None
    assert metrics.coverage_weekend is None
