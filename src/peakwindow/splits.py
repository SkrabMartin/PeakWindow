"""Frozen chronological split of forecast origins (``split-v1``, frozen 2026-10-22).

The boundaries were chosen from the Phase 1 coverage audit, before any model or
baseline was scored, and must not be changed after seeing results. Every model is
evaluated on the same origins. See docs/methodology.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import pandas as pd

from peakwindow.contracts import ContractError

SPLIT_VERSION = "split-v1"


class Period(Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    CALIBRATION = "calibration"
    HOLDOUT = "holdout"


@dataclass(frozen=True)
class PeriodRange:
    """Inclusive range of forecast-origin dates; every origin is 00:00 UTC."""

    first_origin: str
    last_origin: str

    def origins(self) -> pd.DatetimeIndex:
        return pd.date_range(
            pd.Timestamp(self.first_origin, tz="UTC"),
            pd.Timestamp(self.last_origin, tz="UTC"),
            freq="D",
            name="origin_utc",
        )


SPLIT_V1: dict[Period, PeriodRange] = {
    Period.TRAIN: PeriodRange("2015-01-09", "2018-12-31"),
    Period.VALIDATION: PeriodRange("2019-01-01", "2019-06-30"),
    Period.CALIBRATION: PeriodRange("2019-07-01", "2019-12-31"),
    Period.HOLDOUT: PeriodRange("2020-01-01", "2020-09-29"),
}


def origins_for(period: Period) -> pd.DatetimeIndex:
    return SPLIT_V1[period].origins()


def parse_period(name: str) -> Period:
    try:
        return Period(name)
    except ValueError as exc:
        choices = ", ".join(period.value for period in Period)
        raise ContractError(f"unknown period {name!r}; choose one of: {choices}") from exc
