"""Shared helpers: small OPSD-shaped files with synthetic values (no real data, no network)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from peakwindow import opsd
from peakwindow.acquisition import SourceFile, sha256_of
from peakwindow.prepare import output_paths

OpsdWriter = Callable[..., Path]


def opsd_rows(
    start_utc: str = "2019-10-26T20:00:00Z",
    hours: int = 12,
    loads: list[str] | None = None,
) -> pd.DataFrame:
    """Rows shaped like time_series_60min_singleindex.csv, crossing the 2019-10-27 DST change.

    Values are synthetic. Column order mimics the real file, including an unrelated
    country column that the adapter must ignore.
    """
    utc = pd.date_range(start_utc, periods=hours, freq="h")
    local = utc.tz_convert("Europe/Prague")
    values = loads if loads is not None else [f"{6000 + 10 * i:.4f}" for i in range(hours)]
    return pd.DataFrame(
        {
            opsd.UTC_COLUMN: utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            opsd.LOCAL_COLUMN: local.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "AT_load_actual_entsoe_transparency": ["1.0000"] * hours,
            opsd.CZ_LOAD_COLUMN: values,
        }
    )


@pytest.fixture
def write_opsd(tmp_path: Path) -> OpsdWriter:
    """Write OPSD-shaped rows to ``<tmp>/<name>`` and return the path."""

    def write(rows: pd.DataFrame | None = None, name: str = opsd.FILE_NAME) -> Path:
        path = tmp_path / name
        (rows if rows is not None else opsd_rows()).to_csv(path, index=False, lineterminator="\n")
        return path

    return write


def source_for(path: Path) -> SourceFile:
    """A SourceFile describing ``path`` served from a ``file://`` URL."""
    data = path.read_bytes()
    return SourceFile(
        url=path.resolve().as_uri(),
        file_name=opsd.FILE_NAME,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def hourly_series(start_utc: str, end_utc: str, value: Callable[[int], float]) -> pd.DataFrame:
    """A typed canonical series with ``load_mw = value(i)`` for the i-th hour (synthetic)."""
    stamps = pd.date_range(start_utc, end_utc, freq="h")
    return pd.DataFrame(
        {"timestamp_utc": stamps, "load_mw": [float(value(i)) for i in range(len(stamps))]}
    )


def write_processed(series: pd.DataFrame, processed_dir: Path) -> Path:
    """Write ``series`` and a minimal audit record the way ``prepare-opsd`` does."""
    parquet, record = output_paths(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    series.to_parquet(parquet, index=False)
    record.write_text(json.dumps({"processed": {"sha256": sha256_of(parquet)}}), encoding="utf-8")
    return parquet
