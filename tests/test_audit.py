"""Coverage audit: observed span, missing runs, yearly coverage, statistics."""

from __future__ import annotations

import json

import pandas as pd

from peakwindow.audit import MissingRun, YearCoverage, audit_load_series
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN


def reject_constant(name: str) -> None:
    raise ValueError(f"nonstandard JSON constant {name}")


def raw_frame(start: str, loads: list[str]) -> pd.DataFrame:
    stamps = pd.date_range(start, periods=len(loads), freq="h")
    return pd.DataFrame(
        {TIMESTAMP_COLUMN: stamps.strftime("%Y-%m-%dT%H:%M:%SZ"), LOAD_COLUMN: loads}
    )


def test_span_is_trimmed_and_interior_gaps_are_kept_missing() -> None:
    loads = ["", "", "100", "110", "", "", "", "120", "130", "", "140", ""]
    report, series = audit_load_series(raw_frame("2020-01-01T00:00:00Z", loads), source="t")

    assert report.processable
    assert series is not None
    assert report.leading_rows_without_load == 2
    assert report.trailing_rows_without_load == 1
    assert report.observed_first_utc == "2020-01-01T02:00:00Z"
    assert report.observed_last_utc == "2020-01-01T10:00:00Z"
    assert report.observed_hours == len(series) == 9
    assert report.missing_runs == (
        MissingRun("2020-01-01T04:00:00Z", "2020-01-01T06:00:00Z", 3),
        MissingRun("2020-01-01T09:00:00Z", "2020-01-01T09:00:00Z", 1),
    )
    assert report.missing_load_hours_in_span == 4
    assert report.longest_missing_run_hours == 3
    # Missing values are kept, never filled.
    assert int(series[LOAD_COLUMN].isna().sum()) == 4
    assert str(series[TIMESTAMP_COLUMN].dtype).endswith("UTC]")
    assert series[LOAD_COLUMN].dtype == "float64"


def test_yearly_coverage_uses_utc_years() -> None:
    loads = ["1", "", "3", "4"]
    report, _ = audit_load_series(raw_frame("2019-12-31T22:00:00Z", loads), source="t")

    assert report.yearly_coverage == (
        YearCoverage(2019, hours_in_span=2, hours_with_load=1, hours_missing_load=1),
        YearCoverage(2020, hours_in_span=2, hours_with_load=2, hours_missing_load=0),
    )


def test_statistics_and_largest_changes() -> None:
    loads = ["100", "100", "100", "400", "390", "", "50", "60"]
    report, _ = audit_load_series(raw_frame("2020-01-01T00:00:00Z", loads), source="t")

    assert report.load_min_mw == 50.0
    assert report.load_max_mw == 400.0
    assert report.longest_constant_run_hours == 3
    changes = report.largest_hourly_changes
    assert changes is not None
    # The 390 -> 50 drop crosses a missing hour, so it is not a consecutive change.
    assert [(c.timestamp_utc, c.change_mw) for c in changes[:3]] == [
        ("2020-01-01T03:00:00Z", 300.0),
        ("2020-01-01T04:00:00Z", -10.0),
        ("2020-01-01T07:00:00Z", 10.0),
    ]


def test_complete_series_has_no_issues() -> None:
    report, series = audit_load_series(raw_frame("2020-01-01T00:00:00Z", ["1", "2"]), source="t")

    assert report.processable
    assert report.issues == ()
    assert report.missing_runs == ()
    assert series is not None


def test_timestamp_gap_blocks_processing() -> None:
    frame = pd.DataFrame(
        {
            TIMESTAMP_COLUMN: ["2020-01-01T00:00:00Z", "2020-01-01T02:00:00Z"],
            LOAD_COLUMN: ["1", "2"],
        }
    )
    report, series = audit_load_series(frame, source="t")

    assert not report.processable
    assert series is None
    assert any("timestamp grid has gaps" in issue for issue in report.issues)
    assert report.observed_first_utc is None


def test_negative_or_nonnumeric_load_blocks_processing() -> None:
    report, series = audit_load_series(
        raw_frame("2020-01-01T00:00:00Z", ["1", "-5", "x"]), source="t"
    )

    assert series is None
    assert any("negative" in issue for issue in report.issues)
    assert any("nonnumeric" in issue for issue in report.issues)


def test_all_missing_load_blocks_processing() -> None:
    report, series = audit_load_series(raw_frame("2020-01-01T00:00:00Z", ["", ""]), source="t")

    assert series is None
    assert "no load values present" in report.issues[0]


def test_audit_json_is_standard() -> None:
    report, _ = audit_load_series(raw_frame("2020-01-01T00:00:00Z", ["1", "", "3"]), source="t")

    data = json.loads(report.to_json(), parse_constant=reject_constant)

    assert data["schema_version"] == "1"
    assert data["quality"]["missing_load_count"] == 1
    assert data["missing_runs"] == [
        {"start_utc": "2020-01-01T01:00:00Z", "end_utc": "2020-01-01T01:00:00Z", "hours": 1}
    ]
