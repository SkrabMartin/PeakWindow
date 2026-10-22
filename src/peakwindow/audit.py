"""Coverage and quality audit of a canonical hourly load series.

The audit builds on ``validate_hourly_frame`` and adds what its quality report
cannot express for a real source file:

* the *observed span*: first and last hour with a usable load value;
* missing-load runs inside that span, as explicit UTC ranges;
* leading and trailing rows outside the span (e.g. package-wide padding);
* per-calendar-year (UTC) coverage;
* descriptive load statistics and the largest hour-to-hour changes, so suspicious
  spikes are visible. No outlier threshold is applied and no value is changed.

A typed processed series can be built only when every timestamp is valid and the
only load issue is missing values. It is trimmed to the observed span; interior
missing loads stay missing (NaN). Nothing is filled, interpolated or repaired.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from itertools import pairwise
from typing import Any

import pandas as pd

from peakwindow.contracts import HOUR, LOAD_COLUMN, TIMESTAMP_COLUMN
from peakwindow.validation import QualityReport, validate_hourly_frame

AUDIT_SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class MissingRun:
    """Consecutive hours without a load value, with inclusive UTC bounds."""

    start_utc: str
    end_utc: str
    hours: int


@dataclass(frozen=True)
class YearCoverage:
    year: int
    hours_in_span: int
    hours_with_load: int
    hours_missing_load: int


@dataclass(frozen=True)
class HourlyChange:
    """Change between two consecutive hours that both have a load value."""

    timestamp_utc: str
    previous_load_mw: float
    load_mw: float
    change_mw: float


@dataclass(frozen=True)
class AuditReport:
    source: str
    quality: QualityReport
    processable: bool
    observed_first_utc: str | None
    observed_last_utc: str | None
    observed_hours: int | None
    leading_rows_without_load: int | None
    trailing_rows_without_load: int | None
    missing_load_hours_in_span: int | None
    missing_runs: tuple[MissingRun, ...] | None
    longest_missing_run_hours: int | None
    yearly_coverage: tuple[YearCoverage, ...] | None
    load_min_mw: float | None
    load_max_mw: float | None
    load_mean_mw: float | None
    longest_constant_run_hours: int | None
    largest_hourly_changes: tuple[HourlyChange, ...] | None
    issues: tuple[str, ...]
    schema_version: str = AUDIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["quality"] = self.quality.to_dict()
        for key in ("missing_runs", "yearly_coverage", "largest_hourly_changes"):
            value = getattr(self, key)
            data[key] = None if value is None else [asdict(item) for item in value]
        data["issues"] = list(self.issues)
        return {"schema_version": data.pop("schema_version"), **data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, allow_nan=False, ensure_ascii=False)


def _format_utc(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_typed_series(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert a validated raw canonical frame to typed columns.

    ``timestamp_utc`` becomes UTC datetimes and ``load_mw`` float64 with NaN for
    missing values. Call only on frames whose quality report shows valid timestamps
    and no load issue other than missing values.
    """
    load_text = frame[LOAD_COLUMN].astype("string").str.strip()
    load_text = load_text.mask(load_text == "")
    return pd.DataFrame(
        {
            TIMESTAMP_COLUMN: pd.to_datetime(frame[TIMESTAMP_COLUMN], utc=True, format="ISO8601"),
            LOAD_COLUMN: pd.to_numeric(load_text).astype("float64"),
        }
    ).reset_index(drop=True)


def _missing_runs(typed: pd.DataFrame) -> tuple[MissingRun, ...]:
    missing = typed[LOAD_COLUMN].isna()
    run_id = (missing != missing.shift()).cumsum()
    runs = []
    for _, block in typed.loc[missing].groupby(run_id[missing]):
        start, end = block[TIMESTAMP_COLUMN].iloc[0], block[TIMESTAMP_COLUMN].iloc[-1]
        runs.append(MissingRun(_format_utc(start), _format_utc(end), len(block)))
    return tuple(runs)


def _yearly(typed: pd.DataFrame) -> tuple[YearCoverage, ...]:
    years = typed[TIMESTAMP_COLUMN].dt.year
    rows = []
    for year, block in typed.groupby(years):
        present = int(block[LOAD_COLUMN].notna().sum())
        rows.append(YearCoverage(int(str(year)), len(block), present, len(block) - present))
    return tuple(rows)


def _longest_constant_run(load: pd.Series) -> int:
    """Longest run of consecutive hours with an identical (non-missing) load value."""
    present = load.notna()
    if not present.any():
        return 0
    run_id = (load != load.shift()).cumsum()
    return int(load[present].groupby(run_id[present]).size().max())


def _largest_changes(span: pd.DataFrame, count: int) -> tuple[HourlyChange, ...]:
    """The ``count`` largest absolute changes; ties keep chronological order."""
    loads: list[float] = span[LOAD_COLUMN].tolist()
    times: list[pd.Timestamp] = span[TIMESTAMP_COLUMN].tolist()
    candidates = [
        (position, previous, current)
        for position, (previous, current) in enumerate(pairwise(loads), start=1)
        if not (math.isnan(previous) or math.isnan(current))
    ]
    candidates.sort(key=lambda item: -abs(item[2] - item[1]))
    return tuple(
        HourlyChange(
            timestamp_utc=_format_utc(times[position]),
            previous_load_mw=previous,
            load_mw=current,
            change_mw=round(current - previous, 6),
        )
        for position, previous, current in candidates[:count]
    )


def _structurally_processable(quality: QualityReport) -> list[str]:
    """Reasons why a typed processed series cannot be built (empty when it can)."""
    reasons = []
    if quality.valid_timestamp_count is None or quality.missing_load_count is None:
        reasons.append("required columns are missing")
        return reasons
    if quality.valid_timestamp_count != quality.total_rows:
        reasons.append("not every timestamp is a valid UTC hour")
    if quality.duplicate_timestamp_count or quality.valid_timestamps_ordered is False:
        reasons.append("timestamps are duplicated or out of order")
    if quality.missing_hour_count:
        reasons.append("the hourly timestamp grid has gaps")
    if quality.nonnumeric_load_count or quality.nonfinite_load_count:
        reasons.append("load has nonnumeric or infinite values")
    if quality.negative_load_count:
        reasons.append("load has negative values")
    if quality.total_rows == 0:
        reasons.append("input contains no data rows")
    return reasons


def audit_load_series(
    frame: pd.DataFrame, *, source: str, top_changes: int = 10
) -> tuple[AuditReport, pd.DataFrame | None]:
    """Audit a raw canonical frame; return the report and, if possible, the processed series.

    The processed series is trimmed to the observed span (first to last hour with a
    load value). Interior missing values remain NaN.
    """
    quality = validate_hourly_frame(frame, source=source, synthetic=False)
    reasons = _structurally_processable(quality)
    empty: dict[str, Any] = dict.fromkeys(
        (
            "observed_first_utc",
            "observed_last_utc",
            "observed_hours",
            "leading_rows_without_load",
            "trailing_rows_without_load",
            "missing_load_hours_in_span",
            "missing_runs",
            "longest_missing_run_hours",
            "yearly_coverage",
            "load_min_mw",
            "load_max_mw",
            "load_mean_mw",
            "longest_constant_run_hours",
            "largest_hourly_changes",
        )
    )
    if reasons:
        blocked = tuple(f"cannot build processed series: {reason}" for reason in reasons)
        return AuditReport(source, quality, False, issues=blocked, **empty), None

    typed = to_typed_series(frame)
    present: list[bool] = typed[LOAD_COLUMN].notna().tolist()
    if not any(present):
        issue = ("cannot build processed series: no load values present",)
        return AuditReport(source, quality, False, issues=issue, **empty), None

    first = present.index(True)
    last = len(present) - 1 - present[::-1].index(True)
    span = typed.iloc[first : last + 1].reset_index(drop=True)
    runs = _missing_runs(span)
    duration = span[TIMESTAMP_COLUMN].iloc[-1] - span[TIMESTAMP_COLUMN].iloc[0]
    if int(duration / HOUR) + 1 != len(span):  # guaranteed by the quality checks above
        raise AssertionError("processed span is not a contiguous hourly grid")
    load = span[LOAD_COLUMN]

    issues: list[str] = []
    if runs:
        issues.append(
            f"{sum(run.hours for run in runs)} hour(s) without load inside the observed span, "
            f"in {len(runs)} run(s); they are kept as missing, not filled"
        )
    report = AuditReport(
        source=source,
        quality=quality,
        processable=True,
        observed_first_utc=_format_utc(span[TIMESTAMP_COLUMN].iloc[0]),
        observed_last_utc=_format_utc(span[TIMESTAMP_COLUMN].iloc[-1]),
        observed_hours=len(span),
        leading_rows_without_load=first,
        trailing_rows_without_load=len(typed) - 1 - last,
        missing_load_hours_in_span=sum(run.hours for run in runs),
        missing_runs=runs,
        longest_missing_run_hours=max((run.hours for run in runs), default=0),
        yearly_coverage=_yearly(span),
        load_min_mw=float(load.min()),
        load_max_mw=float(load.max()),
        load_mean_mw=round(float(load.mean()), 6),
        longest_constant_run_hours=_longest_constant_run(load),
        largest_hourly_changes=_largest_changes(span, top_changes),
        issues=tuple(issues),
    )
    return report, span
