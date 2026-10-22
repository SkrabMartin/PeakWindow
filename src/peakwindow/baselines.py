"""Seasonal naive baselines, computed from the leakage-safe feature frame.

* ``seasonal_naive_daily``:  ŷ(target) = y(target - 24 h)
* ``seasonal_naive_weekly``: ŷ(target) = y(target - 168 h)

Both read only lag features, so they inherit the builder's pre-origin guarantee.
"""

from __future__ import annotations

import pandas as pd

BASELINES: dict[str, str] = {
    "seasonal_naive_daily": "lag_24h_mw",
    "seasonal_naive_weekly": "lag_168h_mw",
}

REFERENCE_BASELINE = "seasonal_naive_weekly"


def predict(features: pd.DataFrame, name: str) -> list[float]:
    """Return the 24 predictions of baseline ``name`` for one origin's features."""
    try:
        column = BASELINES[name]
    except KeyError as exc:
        raise ValueError(f"unknown baseline {name!r}; choose one of {sorted(BASELINES)}") from exc
    values: list[float] = features[column].astype("float64").tolist()
    return values
