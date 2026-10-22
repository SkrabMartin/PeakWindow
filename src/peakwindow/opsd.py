"""Open Power System Data (OPSD) time-series release 2020-10-06: source definition.

Values below were read from the release's ``datapackage.json``. The declared hash
was confirmed to be SHA-256 by downloading the file and comparing digests (see
docs/data_sources.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from peakwindow.acquisition import SourceFile
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN
from peakwindow.csv_adapter import CsvReadError

RELEASE = "2020-10-06"
DOI_URL = "https://doi.org/10.25832/time_series/2020-10-06"
RELEASE_URL = f"https://data.open-power-system-data.org/time_series/{RELEASE}/"
FILE_NAME = "time_series_60min_singleindex.csv"

SOURCE = SourceFile(
    url=RELEASE_URL + FILE_NAME,
    file_name=FILE_NAME,
    sha256="6a7f2bc571314cbf9c321cc03437691cd4be95c3a6f075e60ff99e8035c704c8",
    size_bytes=130_339_665,
)

# Attribution text prescribed by the release README.
CITATION = (
    "Open Power System Data. 2020. Data Package Time series. Version 2020-10-06. "
    "https://doi.org/10.25832/time_series/2020-10-06. "
    "(Primary data from various sources, for a complete list see URL)."
)

UTC_COLUMN = "utc_timestamp"
LOCAL_COLUMN = "cet_cest_timestamp"
CZ_LOAD_COLUMN = "CZ_load_actual_entsoe_transparency"


def raw_path(data_dir: Path) -> Path:
    """Location of the cached raw file under ``data_dir`` (by default ``data/``)."""
    return data_dir / "raw" / "opsd" / RELEASE / FILE_NAME


PRAGUE = ZoneInfo("Europe/Prague")


@dataclass(frozen=True)
class AlignmentCheck:
    """Agreement between ``utc_timestamp`` and ``cet_cest_timestamp`` in the raw file.

    A row agrees when both columns parse, denote the same instant, and the local
    offset equals the Europe/Prague offset at that instant (CET +01:00 / CEST +02:00).
    """

    rows_checked: int
    unparseable_rows: int
    instant_mismatch_rows: int
    offset_mismatch_rows: int

    @property
    def passed(self) -> bool:
        return not (
            self.unparseable_rows or self.instant_mismatch_rows or self.offset_mismatch_rows
        )


def read_czech_load(path: Path) -> tuple[pd.DataFrame, AlignmentCheck]:
    """Read the Czech load series from the raw OPSD file.

    Returns a frame in the canonical schema (``timestamp_utc``, ``load_mw``) with
    cells kept as raw text for the validator, plus the UTC/local alignment check.
    Only the three needed columns are read; nothing is parsed or filled here.
    """
    columns = [UTC_COLUMN, LOCAL_COLUMN, CZ_LOAD_COLUMN]
    try:
        header = pd.read_csv(path, nrows=0, encoding="utf-8")
        missing = [column for column in columns if column not in header.columns]
        if missing:
            raise CsvReadError(f"{path} is missing expected OPSD column(s): {missing}")
        raw = pd.read_csv(
            path,
            usecols=columns,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        raise CsvReadError(f"input file not found: {path}") from exc
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise CsvReadError(f"cannot parse OPSD file {path}: {exc}") from exc
    except OSError as exc:
        raise CsvReadError(f"cannot read OPSD file {path}: {exc.strerror or exc}") from exc

    canonical = pd.DataFrame({TIMESTAMP_COLUMN: raw[UTC_COLUMN], LOAD_COLUMN: raw[CZ_LOAD_COLUMN]})
    return canonical, check_alignment(raw[UTC_COLUMN].tolist(), raw[LOCAL_COLUMN].tolist())


def check_alignment(utc_values: list[str], local_values: list[str]) -> AlignmentCheck:
    """Compare UTC and CET/CEST labels row by row (see ``AlignmentCheck``)."""
    unparseable = instant_mismatch = offset_mismatch = 0
    for utc_text, local_text in zip(utc_values, local_values, strict=True):
        try:
            utc = datetime.fromisoformat(utc_text)
            local = datetime.fromisoformat(local_text)
        except ValueError:
            unparseable += 1
            continue
        if utc.utcoffset() is None or local.utcoffset() is None:
            unparseable += 1
        elif utc != local:
            instant_mismatch += 1
        elif local.utcoffset() != utc.astimezone(PRAGUE).utcoffset():
            offset_mismatch += 1
    return AlignmentCheck(
        rows_checked=len(utc_values),
        unparseable_rows=unparseable,
        instant_mismatch_rows=instant_mismatch,
        offset_mismatch_rows=offset_mismatch,
    )
