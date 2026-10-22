"""Pure data-quality validation for canonical hourly load frames.

The validator only *measures*. It never sorts, deduplicates, fills gaps or imputes
load in the data it is given. It accepts raw values (strings, as read by the CSV
adapter) as well as already-typed values, and classifies every row.

Counting rules
--------------
Timestamps (column ``timestamp_utc``), each row falls into exactly one class:

* invalid   -- missing, empty, not a string/datetime, or not ISO 8601 parseable
* naive     -- parseable but without a UTC offset (rejected, never guessed)
* off-grid  -- offset-aware, but not on an exact hour boundary after UTC conversion
* valid     -- offset-aware and on an exact hour; normalized to UTC

Duplicates count extra occurrences of a valid timestamp beyond its first.
Ordering is checked on valid timestamps in their original row order and requires
non-decreasing order (duplicates are reported separately). Gaps are computed from
the *unique* valid timestamps, so duplicates cannot hide missing hours.

Load (column ``load_mw``), each row falls into at most one issue class:

* missing     -- null, empty/whitespace-only, or NaN in any spelling (``"nan"``)
* nonnumeric  -- a value that is not a decimal number (booleans included)
* nonfinite   -- positive or negative infinity
* negative    -- a finite value below zero

Zero load is allowed and counted separately for information only.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from itertools import pairwise
from typing import Any

import pandas as pd

from peakwindow.contracts import (
    HOUR,
    LOAD_COLUMN,
    TIMESTAMP_COLUMN,
    ContractError,
    is_on_hour,
    to_utc_timestamp,
)

SCHEMA_VERSION = "1"
MAX_EXAMPLE_ROWS = 5


@dataclass(frozen=True)
class GapRange:
    """A run of consecutive missing hours, with inclusive UTC bounds."""

    start_utc: str
    end_utc: str
    missing_hours: int


@dataclass(frozen=True)
class QualityReport:
    """JSON-serializable quality summary of one hourly load input.

    Count fields are ``None`` when the column they describe is absent; coverage
    fields are ``None`` when there is no valid timestamp to anchor them.
    """

    source: str
    synthetic: bool | None
    total_rows: int
    valid_timestamp_count: int | None
    first_timestamp_utc: str | None
    last_timestamp_utc: str | None
    invalid_timestamp_count: int | None
    naive_timestamp_count: int | None
    off_grid_timestamp_count: int | None
    duplicate_timestamp_count: int | None
    valid_timestamps_ordered: bool | None
    missing_hour_count: int | None
    gap_ranges: tuple[GapRange, ...] | None
    missing_load_count: int | None
    nonnumeric_load_count: int | None
    nonfinite_load_count: int | None
    negative_load_count: int | None
    zero_load_count: int | None
    passed: bool
    issues: tuple[str, ...] = field(default=())
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["issues"] = list(self.issues)
        if self.gap_ranges is not None:
            data["gap_ranges"] = [asdict(gap) for gap in self.gap_ranges]
        # Put the schema version first for readers of the raw JSON.
        return {"schema_version": data.pop("schema_version"), **data}

    def to_json(self) -> str:
        # allow_nan=False guarantees standard JSON; every field is already finite.
        return json.dumps(self.to_dict(), indent=2, allow_nan=False, ensure_ascii=False)


class _TimestampClass(Enum):
    VALID = "valid"
    INVALID = "invalid"
    NAIVE = "naive"
    OFF_GRID = "off_grid"


class _LoadClass(Enum):
    OK = "ok"
    ZERO = "zero"
    MISSING = "missing"
    NONNUMERIC = "nonnumeric"
    NONFINITE = "nonfinite"
    NEGATIVE = "negative"


def _is_missing_scalar(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return value is pd.NA or value is pd.NaT


def _classify_timestamp(value: object) -> tuple[_TimestampClass, pd.Timestamp | None]:
    if _is_missing_scalar(value):
        return _TimestampClass.INVALID, None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return _TimestampClass.INVALID, None
        try:
            parsed: datetime = datetime.fromisoformat(text)
        except ValueError:
            return _TimestampClass.INVALID, None
    elif isinstance(value, datetime):
        parsed = value
    else:
        return _TimestampClass.INVALID, None
    try:
        timestamp = to_utc_timestamp(parsed)
    except ContractError:
        return _TimestampClass.NAIVE, None
    if not is_on_hour(timestamp):
        return _TimestampClass.OFF_GRID, None
    return _TimestampClass.VALID, timestamp


def _classify_load(value: object) -> _LoadClass:
    if _is_missing_scalar(value):
        return _LoadClass.MISSING
    if isinstance(value, bool):
        return _LoadClass.NONNUMERIC
    number: float
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return _LoadClass.MISSING
        # float() accepts digit separators such as "1_000"; a CSV number should not.
        if "_" in text:
            return _LoadClass.NONNUMERIC
        try:
            number = float(text)
        except ValueError:
            return _LoadClass.NONNUMERIC
    elif isinstance(value, int | float):
        number = float(value)
    else:
        try:
            number = float(value)  # type: ignore[arg-type]  # numpy scalars and similar
        except (TypeError, ValueError):
            return _LoadClass.NONNUMERIC
    if math.isnan(number):
        return _LoadClass.MISSING
    if math.isinf(number):
        return _LoadClass.NONFINITE
    if number < 0:
        return _LoadClass.NEGATIVE
    if number == 0:
        return _LoadClass.ZERO
    return _LoadClass.OK


def _format_utc(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows_hint(rows: Sequence[int]) -> str:
    shown = ", ".join(str(row) for row in rows[:MAX_EXAMPLE_ROWS])
    more = "" if len(rows) <= MAX_EXAMPLE_ROWS else ", ..."
    return f" (data rows {shown}{more})"


def _find_gaps(unique_sorted: Sequence[pd.Timestamp]) -> tuple[GapRange, ...]:
    gaps: list[GapRange] = []
    for previous, current in pairwise(unique_sorted):
        missing = int((current - previous) / HOUR) - 1
        if missing > 0:
            gaps.append(
                GapRange(
                    start_utc=_format_utc(previous + HOUR),
                    end_utc=_format_utc(current - HOUR),
                    missing_hours=missing,
                )
            )
    return tuple(gaps)


@dataclass
class _TimestampStats:
    valid: list[pd.Timestamp]
    invalid_rows: list[int]
    naive_rows: list[int]
    off_grid_rows: list[int]


def _scan_timestamps(values: Sequence[object]) -> _TimestampStats:
    stats = _TimestampStats(valid=[], invalid_rows=[], naive_rows=[], off_grid_rows=[])
    buckets = {
        _TimestampClass.INVALID: stats.invalid_rows,
        _TimestampClass.NAIVE: stats.naive_rows,
        _TimestampClass.OFF_GRID: stats.off_grid_rows,
    }
    for row, value in enumerate(values, start=1):
        kind, timestamp = _classify_timestamp(value)
        if timestamp is not None:
            stats.valid.append(timestamp)
        else:
            buckets[kind].append(row)
    return stats


def validate_hourly_frame(
    frame: pd.DataFrame, *, source: str, synthetic: bool | None = None
) -> QualityReport:
    """Validate a canonical hourly load frame and return a quality report.

    ``frame`` must provide the columns ``timestamp_utc`` and ``load_mw``; extra
    columns are ignored. The frame is not modified. ``synthetic`` records the
    caller's declaration of data provenance (``None`` means undeclared).
    """
    issues: list[str] = []
    total_rows = len(frame)
    missing_columns = [
        column for column in (TIMESTAMP_COLUMN, LOAD_COLUMN) if column not in frame.columns
    ]
    if missing_columns:
        issues.append(
            "missing required column(s): "
            + ", ".join(repr(column) for column in missing_columns)
            + f"; found {[str(column) for column in frame.columns]}"
        )
    if total_rows == 0:
        issues.append("input contains no data rows")

    ts: dict[str, Any] = dict.fromkeys(
        (
            "valid_timestamp_count",
            "first_timestamp_utc",
            "last_timestamp_utc",
            "invalid_timestamp_count",
            "naive_timestamp_count",
            "off_grid_timestamp_count",
            "duplicate_timestamp_count",
            "valid_timestamps_ordered",
            "missing_hour_count",
            "gap_ranges",
        )
    )
    if TIMESTAMP_COLUMN in frame.columns:
        stats = _scan_timestamps(frame[TIMESTAMP_COLUMN].tolist())
        unique_sorted = sorted(set(stats.valid))
        duplicates = len(stats.valid) - len(unique_sorted)
        ts.update(
            valid_timestamp_count=len(stats.valid),
            invalid_timestamp_count=len(stats.invalid_rows),
            naive_timestamp_count=len(stats.naive_rows),
            off_grid_timestamp_count=len(stats.off_grid_rows),
            duplicate_timestamp_count=duplicates,
        )
        if stats.invalid_rows:
            issues.append(
                f"{len(stats.invalid_rows)} timestamp(s) missing or not ISO 8601"
                + _rows_hint(stats.invalid_rows)
            )
        if stats.naive_rows:
            issues.append(
                f"{len(stats.naive_rows)} timezone-naive timestamp(s) rejected; "
                "an explicit UTC offset is required" + _rows_hint(stats.naive_rows)
            )
        if stats.off_grid_rows:
            issues.append(
                f"{len(stats.off_grid_rows)} timestamp(s) not on an exact UTC hour"
                + _rows_hint(stats.off_grid_rows)
            )
        if duplicates:
            issues.append(f"{duplicates} duplicate timestamp occurrence(s) beyond the first")
        if unique_sorted:
            ordered = all(a <= b for a, b in pairwise(stats.valid))
            gaps = _find_gaps(unique_sorted)
            missing_hours = sum(gap.missing_hours for gap in gaps)
            ts.update(
                first_timestamp_utc=_format_utc(unique_sorted[0]),
                last_timestamp_utc=_format_utc(unique_sorted[-1]),
                valid_timestamps_ordered=ordered,
                missing_hour_count=missing_hours,
                gap_ranges=gaps,
            )
            if not ordered:
                issues.append("valid timestamps are not in chronological row order")
            if gaps:
                issues.append(
                    f"{missing_hours} missing hourly timestamp(s) in {len(gaps)} gap(s), "
                    f"first gap {gaps[0].start_utc} .. {gaps[0].end_utc}"
                )
        elif total_rows:
            issues.append("no valid timestamps; coverage cannot be determined")

    load: dict[str, int | None] = dict.fromkeys(
        ("missing", "nonnumeric", "nonfinite", "negative", "zero")
    )
    if LOAD_COLUMN in frame.columns:
        rows_by_class: dict[_LoadClass, list[int]] = {kind: [] for kind in _LoadClass}
        for row, value in enumerate(frame[LOAD_COLUMN].tolist(), start=1):
            rows_by_class[_classify_load(value)].append(row)
        load = {
            kind.value: len(rows_by_class[kind]) for kind in _LoadClass if kind is not _LoadClass.OK
        }
        messages = {
            _LoadClass.MISSING: "missing load value(s)",
            _LoadClass.NONNUMERIC: "nonnumeric load value(s)",
            _LoadClass.NONFINITE: "infinite load value(s)",
            _LoadClass.NEGATIVE: "negative load value(s)",
        }
        for kind, message in messages.items():
            rows = rows_by_class[kind]
            if rows:
                issues.append(f"{len(rows)} {message}" + _rows_hint(rows))

    return QualityReport(
        source=source,
        synthetic=synthetic,
        total_rows=total_rows,
        missing_load_count=load["missing"],
        nonnumeric_load_count=load["nonnumeric"],
        nonfinite_load_count=load["nonfinite"],
        negative_load_count=load["negative"],
        zero_load_count=load["zero"],
        passed=not issues,
        issues=tuple(issues),
        **ts,
    )
