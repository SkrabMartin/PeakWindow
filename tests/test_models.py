"""Ridge forecaster: fitting, prediction, export and feature encoding."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from peakwindow.models import SPECS, RidgeForecaster, design_matrix


def feature_frame(origins: int = 60, seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    """Synthetic feature rows; the target is an exact linear function of the inputs."""
    rng = np.random.default_rng(seed)
    rows = origins * 24
    frame = pd.DataFrame(
        {
            "horizon_h": np.tile(np.arange(24), origins),
            "local_hour": np.tile(np.arange(24) + 1, origins),
            "local_weekday": np.repeat(np.arange(origins) % 7, 24),
            "month": np.repeat(np.arange(origins) % 12 + 1, 24),
            "is_weekend": np.repeat(np.arange(origins) % 7 >= 5, 24),
            "is_holiday": np.repeat(np.arange(origins) % 17 == 0, 24),
            "lag_24h_is_holiday": np.repeat(np.arange(origins) % 17 == 1, 24),
            "lag_168h_is_holiday": np.repeat(np.arange(origins) % 17 == 7, 24),
            "lag_24h_mw": rng.uniform(5000, 9000, rows),
            "lag_168h_mw": rng.uniform(5000, 9000, rows),
            "last_load_mw": rng.uniform(5000, 9000, rows),
            "prev_24h_mean_mw": rng.uniform(5000, 9000, rows),
        }
    )
    target = (
        0.4 * frame["lag_24h_mw"]
        + 0.5 * frame["lag_168h_mw"]
        + 0.1 * frame["last_load_mw"]
        - 500 * frame["is_holiday"]
        + 10 * frame["horizon_h"]
    )
    return frame, target


@pytest.mark.parametrize("spec", SPECS)
def test_ridge_recovers_a_linear_relation(spec: str) -> None:
    frame, target = feature_frame()
    model = RidgeForecaster(alpha=1e-6, spec=spec).fit(frame, target)

    test_frame, test_target = feature_frame(seed=1)
    error = np.abs(model.predict(test_frame) - test_target.to_numpy())

    assert error.max() < 1.0


def test_export_reproduces_predictions() -> None:
    frame, target = feature_frame()
    model = RidgeForecaster(alpha=10.0, spec="weekday_interactions").fit(frame, target)
    exported = model.export()

    matrix = design_matrix(frame, "weekday_interactions")
    assert exported["columns"] == list(matrix.columns)
    manual = np.empty(len(frame))
    for item in exported["horizons"]:
        rows = (frame["horizon_h"] == item["horizon_h"]).to_numpy()
        scaled = (matrix.to_numpy()[rows] - item["scaler_mean"]) / item["scaler_scale"]
        manual[rows] = scaled @ np.asarray(item["coefficients"]) + item["intercept"]

    np.testing.assert_allclose(manual, model.predict(frame), rtol=1e-9)
    assert len(exported["horizons"]) == 24
    assert exported["alpha"] == 10.0


def test_design_matrix_has_no_target_or_identity_columns() -> None:
    frame, target = feature_frame()
    matrix = design_matrix(frame.assign(actual=target, target_utc=0), "weekday_interactions")

    assert not {"actual", "target_utc", "origin_utc", "horizon_h"} & set(matrix.columns)
    assert matrix.notna().all().all()


def test_misuse_is_rejected() -> None:
    frame, target = feature_frame()
    with pytest.raises(ValueError, match="unknown feature spec"):
        design_matrix(frame, "deep")
    with pytest.raises(RuntimeError, match="not fitted"):
        RidgeForecaster(alpha=1.0).predict(frame)
    with pytest.raises(RuntimeError, match="not fitted"):
        RidgeForecaster(alpha=1.0).export()
    partial = frame["horizon_h"] < 23
    with pytest.raises(ValueError, match="horizon 23"):
        RidgeForecaster(alpha=1.0).fit(frame[partial], target[partial])
