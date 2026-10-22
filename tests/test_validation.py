"""Data-quality validator and canonical CSV adapter tests."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN
from peakwindow.csv_adapter import CsvReadError, read_canonical_csv
from peakwindow.validation import GapRange, QualityReport, validate_hourly_frame

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "synthetic_hourly_load.csv"


def synthetic_fixture_rows() -> list[tuple[str, str]]:
    """The documented generation rule for the committed fixture (data/fixtures/README.md)."""
    start = datetime(2019, 3, 25, tzinfo=UTC)
    rows = []
    for i in range(240):
        t = start + timedelta(hours=i)
        load = (
            7200
            + round(1300 * math.sin(2 * math.pi * (t.hour - 6) / 24))
            - (700 if t.weekday() >= 5 else 0)
            + ((i * 37) % 11 - 5) * 15
        )
        rows.append((f"{t:%Y-%m-%dT%H:%M:%SZ}", str(load)))
    return rows


def frame_of(timestamps: list[object], loads: list[object] | None = None) -> pd.DataFrame:
    loads = loads if loads is not None else ["7000"] * len(timestamps)
    return pd.DataFrame({TIMESTAMP_COLUMN: timestamps, LOAD_COLUMN: loads}, dtype=object)


def hours(start: str, count: int) -> list[object]:
    first = pd.Timestamp(start)
    return [(first + pd.Timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ") for i in range(count)]


def validate(frame: pd.DataFrame) -> QualityReport:
    return validate_hourly_frame(frame, source="test", synthetic=True)


# --- committed fixture ------------------------------------------------------


def test_fixture_matches_documented_generation_rule() -> None:
    frame = read_canonical_csv(FIXTURE)
    assert list(frame.columns) == [TIMESTAMP_COLUMN, LOAD_COLUMN]
    assert list(frame.itertuples(index=False, name=None)) == synthetic_fixture_rows()


def test_fixture_passes_with_full_coverage_and_no_gaps() -> None:
    report = validate_hourly_frame(read_canonical_csv(FIXTURE), source="fixture", synthetic=True)

    assert report.passed, report.issues
    assert report.synthetic is True
    assert report.total_rows == report.valid_timestamp_count == 240
    assert report.first_timestamp_utc == "2019-03-25T00:00:00Z"
    assert report.last_timestamp_utc == "2019-04-03T23:00:00Z"
    assert report.missing_hour_count == 0
    assert report.gap_ranges == ()
    assert report.duplicate_timestamp_count == 0
    assert report.valid_timestamps_ordered is True
    assert report.issues == ()


# --- timestamp rules --------------------------------------------------------


def test_duplicates_count_extra_occurrences_and_cannot_hide_gaps() -> None:
    # 00, 01, 01, 01, 03 -> two extra occurrences of 01:00 and a missing 02:00.
    timestamps = [*hours("2020-01-01T00:00:00Z", 2), *hours("2020-01-01T01:00:00Z", 1) * 2]
    timestamps.append("2020-01-01T03:00:00Z")
    report = validate(frame_of(timestamps))

    assert not report.passed
    assert report.duplicate_timestamp_count == 2
    assert report.valid_timestamp_count == 5
    assert report.missing_hour_count == 1
    assert report.gap_ranges == (GapRange("2020-01-01T02:00:00Z", "2020-01-01T02:00:00Z", 1),)
    assert report.valid_timestamps_ordered is True


def test_disorder_is_reported_not_sorted_away() -> None:
    timestamps = hours("2020-01-01T00:00:00Z", 4)
    timestamps[1], timestamps[2] = timestamps[2], timestamps[1]
    frame = frame_of(timestamps)
    before = frame.copy()

    report = validate(frame)

    assert report.valid_timestamps_ordered is False
    assert report.missing_hour_count == 0  # coverage is still complete
    assert any("chronological" in issue for issue in report.issues)
    assert not report.passed
    pd.testing.assert_frame_equal(frame, before)  # input untouched


def test_hourly_gap_ranges_are_explicit() -> None:
    timestamps = [
        *hours("2020-01-01T00:00:00Z", 3),
        *hours("2020-01-01T06:00:00Z", 2),
        *hours("2020-01-01T09:00:00Z", 1),
    ]
    report = validate(frame_of(timestamps))

    assert report.missing_hour_count == 4
    assert report.gap_ranges == (
        GapRange("2020-01-01T03:00:00Z", "2020-01-01T05:00:00Z", 3),
        GapRange("2020-01-01T08:00:00Z", "2020-01-01T08:00:00Z", 1),
    )
    assert not report.passed


def test_invalid_naive_and_off_grid_timestamps_are_classified() -> None:
    timestamps: list[object] = [
        "2020-01-01T00:00:00Z",  # valid
        "2020-01-01T01:00:00",  # naive
        "2020-01-01 02:00:00",  # naive (space separator is still ISO 8601)
        "2020-01-01T03:30:00Z",  # off-grid
        "2020-01-01T04:00:01+00:00",  # off-grid
        "01/01/2020 05:00",  # invalid format
        "",  # missing
        None,  # missing
        "2020-01-01T06:00:00Z",  # valid
    ]
    report = validate(frame_of(timestamps))

    assert report.valid_timestamp_count == 2
    assert report.naive_timestamp_count == 2
    assert report.off_grid_timestamp_count == 2
    assert report.invalid_timestamp_count == 3
    assert report.first_timestamp_utc == "2020-01-01T00:00:00Z"
    assert report.last_timestamp_utc == "2020-01-01T06:00:00Z"
    # Coverage is measured on valid timestamps only: 01..05 are missing.
    assert report.missing_hour_count == 5
    assert not report.passed
    naive_issue = next(issue for issue in report.issues if "naive" in issue)
    assert "data rows 2, 3" in naive_issue


def test_offset_aware_timestamps_are_normalized_to_utc() -> None:
    timestamps = ["2020-01-01T01:00:00+01:00", "2020-01-01T01:00:00Z", "2020-01-01T02:00:00Z"]
    report = validate(frame_of(timestamps))

    assert report.passed, report.issues
    assert report.first_timestamp_utc == "2020-01-01T00:00:00Z"


def test_offset_normalization_exposes_hidden_duplicates() -> None:
    # 01:00+01:00 and 00:00Z are the same instant.
    report = validate(frame_of(["2020-01-01T00:00:00Z", "2020-01-01T01:00:00+01:00"]))

    assert report.duplicate_timestamp_count == 1
    assert not report.passed


def test_typed_timestamps_are_accepted() -> None:
    timestamps = pd.date_range("2020-01-01", periods=3, freq="h", tz="UTC")
    frame = pd.DataFrame({TIMESTAMP_COLUMN: timestamps, LOAD_COLUMN: [7000.0, 7100.0, 0.0]})

    report = validate(frame)

    assert report.passed, report.issues
    assert report.zero_load_count == 1


def test_typed_naive_timestamps_are_rejected() -> None:
    timestamps = pd.date_range("2020-01-01", periods=3, freq="h")
    frame = pd.DataFrame({TIMESTAMP_COLUMN: timestamps, LOAD_COLUMN: [1.0, 2.0, 3.0]})

    report = validate(frame)

    assert report.naive_timestamp_count == 3
    assert report.valid_timestamp_count == 0
    assert report.first_timestamp_utc is None
    assert report.missing_hour_count is None
    assert report.gap_ranges is None
    assert any("coverage cannot be determined" in issue for issue in report.issues)


@pytest.mark.parametrize(
    ("local_day", "expected_local_hours"),
    [
        ("2019-03-31", 23),  # spring forward: 02:00 Prague does not exist
        ("2019-10-27", 25),  # fall back: 02:00 Prague occurs twice
    ],
)
def test_utc_index_is_unambiguous_across_prague_dst(
    local_day: str, expected_local_hours: int
) -> None:
    day_start = pd.Timestamp(local_day, tz="Europe/Prague")
    next_day = day_start + pd.offsets.Day(1)
    utc_hours = pd.date_range(day_start.tz_convert("UTC"), next_day.tz_convert("UTC"), freq="h")
    utc_hours = utc_hours[:-1]
    local_labels = utc_hours.tz_convert("Europe/Prague")

    # The local calendar day really has an irregular number of hours...
    assert len(utc_hours) == expected_local_hours
    assert local_labels.strftime("%H:%M").duplicated().any() == (expected_local_hours == 25)

    # ...but written with explicit offsets it validates as a gap-free, unique UTC series.
    as_text = [timestamp.isoformat() for timestamp in local_labels]
    report = validate(frame_of(as_text))

    assert report.passed, report.issues
    assert report.valid_timestamp_count == expected_local_hours
    assert report.duplicate_timestamp_count == 0
    assert report.missing_hour_count == 0


# --- load rules -------------------------------------------------------------


def test_load_value_classes_follow_documented_rules() -> None:
    loads: list[object] = [
        "7000",  # ok
        "6999.5",  # ok
        "0",  # zero: allowed, informational
        "",  # missing
        "   ",  # missing
        "NaN",  # missing (NaN in any spelling)
        None,  # missing
        "abc",  # nonnumeric
        "1_000",  # nonnumeric (digit separators are not CSV numbers)
        "7,000",  # nonnumeric
        "inf",  # nonfinite
        "-Infinity",  # nonfinite
        "-5",  # negative
        "-0.0",  # zero, not negative
    ]
    report = validate(frame_of(hours("2020-01-01T00:00:00Z", len(loads)), loads))

    assert report.missing_load_count == 4
    assert report.nonnumeric_load_count == 3
    assert report.nonfinite_load_count == 2
    assert report.negative_load_count == 1
    assert report.zero_load_count == 2
    assert not report.passed
    assert len(report.issues) == 4


def test_typed_float_nan_and_infinity() -> None:
    frame = frame_of(hours("2020-01-01T00:00:00Z", 4), [7000.0, math.nan, math.inf, -1.0])

    report = validate(frame)

    assert report.missing_load_count == 1
    assert report.nonfinite_load_count == 1
    assert report.negative_load_count == 1


def test_zero_load_alone_passes() -> None:
    report = validate(frame_of(hours("2020-01-01T00:00:00Z", 3), ["0", "0.0", "7000"]))

    assert report.passed, report.issues
    assert report.zero_load_count == 2


def test_boolean_load_is_nonnumeric() -> None:
    report = validate(frame_of(hours("2020-01-01T00:00:00Z", 1), [True]))

    assert report.nonnumeric_load_count == 1


# --- structure --------------------------------------------------------------


def test_missing_columns_fail_with_readable_issue() -> None:
    frame = pd.DataFrame({"utc_timestamp": ["2020-01-01T00:00:00Z"], "load": ["1"]})

    report = validate(frame)

    assert not report.passed
    assert "missing required column(s): 'timestamp_utc', 'load_mw'" in report.issues[0]
    assert report.valid_timestamp_count is None
    assert report.missing_load_count is None


def test_empty_frame_fails() -> None:
    report = validate(frame_of([], []))

    assert not report.passed
    assert report.total_rows == 0
    assert report.first_timestamp_utc is None
    assert report.missing_hour_count is None
    assert "input contains no data rows" in report.issues


def test_report_json_is_standard_and_stable() -> None:
    frame = frame_of(hours("2020-01-01T00:00:00Z", 3), ["NaN", "inf", "7000"])
    report = validate(frame)

    text = report.to_json()
    data = json.loads(text, parse_constant=lambda name: pytest.fail(f"nonstandard {name}"))

    assert data["schema_version"] == "1"
    assert next(iter(data)) == "schema_version"
    assert data["synthetic"] is True
    assert data["passed"] is False
    assert data["gap_ranges"] == []
    assert isinstance(data["issues"], list)
    assert set(data) == {
        "schema_version",
        "source",
        "synthetic",
        "total_rows",
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
        "missing_load_count",
        "nonnumeric_load_count",
        "nonfinite_load_count",
        "negative_load_count",
        "zero_load_count",
        "passed",
        "issues",
    }


# --- CSV adapter ------------------------------------------------------------


def test_csv_adapter_keeps_raw_text(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"
    path.write_text("timestamp_utc,load_mw\n2020-01-01T00:00:00Z,NaN\n2020-01-01T01:00:00Z,\n")

    frame = read_canonical_csv(path)

    assert frame[LOAD_COLUMN].tolist() == ["NaN", ""]


def test_csv_adapter_header_only_file_yields_failed_report(tmp_path: Path) -> None:
    path = tmp_path / "header_only.csv"
    path.write_text("timestamp_utc,load_mw\n")

    report = validate(read_canonical_csv(path))

    assert not report.passed
    assert report.total_rows == 0


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "empty"),
        (b'timestamp_utc,load_mw\n"2020-01-01T00:00:00Z,1\n', "not well-formed CSV"),
        (b"timestamp_utc,load_mw\n2020-01-01T00:00:00Z,\xff\xfe\n", "not valid UTF-8"),
    ],
)
def test_csv_adapter_structural_errors(tmp_path: Path, content: bytes, message: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_bytes(content)

    with pytest.raises(CsvReadError, match=message):
        read_canonical_csv(path)


def test_csv_adapter_missing_file_and_directory(tmp_path: Path) -> None:
    with pytest.raises(CsvReadError, match="not found"):
        read_canonical_csv(tmp_path / "absent.csv")
    with pytest.raises(CsvReadError, match="not a file"):
        read_canonical_csv(tmp_path)
