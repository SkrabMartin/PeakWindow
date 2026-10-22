"""Typed contracts for historical hourly load and 24-hour forecast requests.

Time semantics (see docs/methodology.md):

* ``timestamp_utc`` is a timezone-aware UTC instant on an exact hour boundary.
  It labels the *start* of the hourly interval it describes. Whether this matches
  the interval convention of the eventual OPSD source must be re-verified during
  ingestion.
* Offset-aware inputs (for example ``+01:00``) are normalized to UTC. Naive
  timestamps are rejected; their timezone is never guessed.
* A forecast issued at ``origin_utc`` (always 00:00 UTC) targets
  ``origin_utc + h`` hours for ``h = 0, ..., 23``. Only observations with
  ``timestamp_utc < origin_utc`` are eligible as model inputs; the observation at
  the origin itself is a target.

Event time before the origin does not prove that a value was *published* before
issuance. PeakWindow assumes immediate availability of prior observations; real
publication delays are an unresolved limitation until audited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

TIMESTAMP_COLUMN = "timestamp_utc"
LOAD_COLUMN = "load_mw"
CANONICAL_COLUMNS: tuple[str, str] = (TIMESTAMP_COLUMN, LOAD_COLUMN)

HORIZON_HOURS = 24
HOUR = pd.Timedelta(hours=1)


class ContractError(ValueError):
    """Raised when an input violates a PeakWindow data or forecast contract."""


def to_utc_timestamp(value: datetime | str, *, name: str = "timestamp") -> pd.Timestamp:
    """Convert an offset-aware datetime or ISO 8601 string to a UTC ``pd.Timestamp``.

    Naive values are rejected because their timezone would have to be guessed.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ContractError(f"{name} is not a valid ISO 8601 timestamp: {value!r}") from exc
    if not isinstance(value, datetime):
        raise ContractError(f"{name} must be a datetime or ISO 8601 string, got {type(value)!r}")
    if pd.isna(value):
        raise ContractError(f"{name} is missing (NaT)")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(
            f"{name} is timezone-naive ({value.isoformat()}); provide an explicit UTC offset"
        )
    return pd.Timestamp(value).tz_convert("UTC")


def is_on_hour(timestamp: pd.Timestamp) -> bool:
    """Return True when ``timestamp`` lies exactly on an hour boundary."""
    return timestamp == timestamp.floor("h")


@dataclass(frozen=True)
class ForecastRequest:
    """A historical next-24-hour forecast request issued at ``origin_utc``.

    ``origin_utc`` is normalized to UTC and must be exactly 00:00:00 UTC.
    ``horizon_hours`` is fixed at 24; other values are rejected.
    """

    origin_utc: pd.Timestamp
    horizon_hours: int = HORIZON_HOURS

    def __post_init__(self) -> None:
        origin = to_utc_timestamp(self.origin_utc, name="origin_utc")
        if origin != origin.normalize():
            raise ContractError(f"origin_utc must be exactly 00:00 UTC, got {origin.isoformat()}")
        # bool is an int subclass; reject it explicitly.
        if isinstance(self.horizon_hours, bool) or not isinstance(self.horizon_hours, int):
            raise ContractError(
                f"horizon_hours must be the integer {HORIZON_HOURS}, got {self.horizon_hours!r}"
            )
        if self.horizon_hours != HORIZON_HOURS:
            raise ContractError(
                f"unsupported horizon_hours={self.horizon_hours}; only {HORIZON_HOURS} is supported"
            )
        object.__setattr__(self, "origin_utc", origin)

    @classmethod
    def from_value(cls, origin: datetime | str) -> ForecastRequest:
        """Build a request from an offset-aware datetime or ISO 8601 string."""
        return cls(origin_utc=to_utc_timestamp(origin, name="origin_utc"))


def target_timestamps(request: ForecastRequest) -> pd.DatetimeIndex:
    """Return the 24 ordered UTC target timestamps ``origin + h`` for ``h = 0..23``."""
    return pd.date_range(
        start=request.origin_utc,
        periods=request.horizon_hours,
        freq=HOUR,
        name=TIMESTAMP_COLUMN,
    )


def _require_utc_timestamp_column(series: pd.DataFrame) -> None:
    if TIMESTAMP_COLUMN not in series.columns:
        raise ContractError(f"history frame is missing required column {TIMESTAMP_COLUMN!r}")
    dtype = series[TIMESTAMP_COLUMN].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC":
        raise ContractError(
            f"{TIMESTAMP_COLUMN!r} must have a timezone-aware UTC datetime dtype, got {dtype}"
        )


def select_history(series: pd.DataFrame, request: ForecastRequest) -> pd.DataFrame:
    """Return the rows eligible as model inputs: ``timestamp_utc < origin_utc``.

    The observation exactly at the origin and everything after it are excluded.
    Row order and index are preserved; nothing is sorted, deduplicated or filled.
    This establishes the history-selection boundary only; it does not by itself
    prove that later feature generation is leakage-free.
    """
    _require_utc_timestamp_column(series)
    eligible = series[TIMESTAMP_COLUMN] < request.origin_utc
    return series.loc[eligible].copy()
