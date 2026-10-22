"""Leakage-safe features for one forecast origin: 24 rows, one per target hour.

The builder receives the full series but first restricts it with
``select_history()``, so every load-derived value comes from hours strictly before
the origin. Calendar features describe the *target* timestamps, which are known
when the forecast is issued, so they carry no information about future load.

Columns (in order):

* ``origin_utc``, ``target_utc``, ``horizon_h`` (0..23)
* ``local_hour``, ``local_weekday`` (0 = Monday), ``month``, ``is_weekend``:
  the target's Europe/Prague calendar, because demand follows local time
* ``lag_24h_mw``: load at ``target - 24 h`` (same local-clock hour yesterday,
  except across daylight-saving changes)
* ``lag_168h_mw``: load at ``target - 168 h`` (same hour one week earlier)
* ``last_load_mw``: load at ``origin - 1 h``, the latest available value
* ``prev_24h_mean_mw``: mean load over ``origin - 24 h .. origin - 1 h``; missing
  if any of those 24 hours is missing (no partial means)

Missing inputs stay NaN. Origins with any NaN feature are excluded from evaluation
as data-gap exceptions, never filled.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

from peakwindow.contracts import (
    HOUR,
    LOAD_COLUMN,
    TIMESTAMP_COLUMN,
    ForecastRequest,
    select_history,
    target_timestamps,
)

PRAGUE = ZoneInfo("Europe/Prague")

CALENDAR_FEATURES = ("local_hour", "local_weekday", "month", "is_weekend")
LOAD_FEATURES = ("lag_24h_mw", "lag_168h_mw", "last_load_mw", "prev_24h_mean_mw")
FEATURE_COLUMNS = CALENDAR_FEATURES + LOAD_FEATURES
KEY_COLUMNS = ("origin_utc", "target_utc", "horizon_h")


def _lookup(history: pd.Series, timestamps: pd.DatetimeIndex) -> list[float]:
    values: list[float] = history.reindex(timestamps).astype("float64").tolist()
    return values


def build_features(series: pd.DataFrame, request: ForecastRequest) -> pd.DataFrame:
    """Return the 24-row feature frame for ``request`` using only pre-origin data."""
    history = select_history(series, request)
    by_time = history.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    if not by_time.index.is_unique:
        raise ValueError("history has duplicate timestamps; validate the series first")

    targets = target_timestamps(request)
    origin = request.origin_utc
    local = targets.tz_convert(PRAGUE)
    previous_day = pd.date_range(origin - 24 * HOUR, periods=24, freq=HOUR)
    previous_values = _lookup(by_time, previous_day)
    previous_mean = (
        float("nan")
        if any(pd.isna(value) for value in previous_values)
        else sum(previous_values) / len(previous_values)
    )

    return pd.DataFrame(
        {
            "origin_utc": [origin] * len(targets),
            "target_utc": targets,
            "horizon_h": range(len(targets)),
            "local_hour": local.hour,
            "local_weekday": local.day_of_week,
            "month": local.month,
            "is_weekend": local.day_of_week >= 5,
            "lag_24h_mw": _lookup(by_time, targets - 24 * HOUR),
            "lag_168h_mw": _lookup(by_time, targets - 168 * HOUR),
            "last_load_mw": _lookup(by_time, pd.DatetimeIndex([origin - HOUR])) * len(targets),
            "prev_24h_mean_mw": [previous_mean] * len(targets),
        }
    )
