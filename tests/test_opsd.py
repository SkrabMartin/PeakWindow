"""OPSD adapter: column mapping and UTC vs CET/CEST alignment (synthetic values only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from peakwindow import opsd
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN
from peakwindow.csv_adapter import CsvReadError
from peakwindow.validation import validate_hourly_frame

from conftest import OpsdWriter, opsd_rows


def test_maps_czech_columns_to_canonical_schema(write_opsd: OpsdWriter) -> None:
    frame, alignment = opsd.read_czech_load(write_opsd())

    assert list(frame.columns) == [TIMESTAMP_COLUMN, LOAD_COLUMN]
    assert frame[TIMESTAMP_COLUMN].iloc[0] == "2019-10-26T20:00:00Z"
    assert frame[LOAD_COLUMN].iloc[0] == "6000.0000"
    assert alignment.passed
    assert alignment.rows_checked == 12


def test_real_timestamp_format_validates_across_dst(write_opsd: OpsdWriter) -> None:
    frame, _ = opsd.read_czech_load(write_opsd())

    report = validate_hourly_frame(frame, source="excerpt")

    assert report.passed, report.issues
    assert report.duplicate_timestamp_count == 0


def test_empty_czech_cells_stay_raw(write_opsd: OpsdWriter) -> None:
    rows = opsd_rows(hours=3, loads=["", "6000.0000", ""])
    frame, _ = opsd.read_czech_load(write_opsd(rows))

    assert frame[LOAD_COLUMN].tolist() == ["", "6000.0000", ""]


def test_alignment_detects_wrong_local_offset(write_opsd: OpsdWriter) -> None:
    rows = opsd_rows()
    # Rows 8 and 9 are 04:00Z and 05:00Z, after the fall-back change (CET, +0100).
    rows.loc[8, opsd.LOCAL_COLUMN] = "2019-10-27T06:00:00+0200"  # 04:00Z, but labeled CEST
    rows.loc[9, opsd.LOCAL_COLUMN] = "2019-10-27T05:00:00+0200"  # 03:00Z: wrong instant

    _, alignment = opsd.read_czech_load(write_opsd(rows))

    assert not alignment.passed
    assert alignment.instant_mismatch_rows == 1
    assert alignment.offset_mismatch_rows == 1


def test_alignment_counts_unparseable_and_naive(write_opsd: OpsdWriter) -> None:
    rows = opsd_rows(hours=3)
    rows.loc[0, opsd.LOCAL_COLUMN] = "garbage"
    rows.loc[1, opsd.LOCAL_COLUMN] = "2019-10-26T23:00:00"

    _, alignment = opsd.read_czech_load(write_opsd(rows))

    assert alignment.unparseable_rows == 2


def test_missing_opsd_column_is_a_read_error(write_opsd: OpsdWriter) -> None:
    rows = opsd_rows().drop(columns=[opsd.CZ_LOAD_COLUMN])

    with pytest.raises(CsvReadError, match=opsd.CZ_LOAD_COLUMN):
        opsd.read_czech_load(write_opsd(rows))


def test_missing_file_is_a_read_error(tmp_path: Path) -> None:
    with pytest.raises(CsvReadError, match="not found"):
        opsd.read_czech_load(tmp_path / "absent.csv")


def test_raw_path_layout() -> None:
    path = opsd.raw_path(Path("data"))
    assert path.as_posix() == "data/raw/opsd/2020-10-06/time_series_60min_singleindex.csv"
