"""Offline preparation of the Czech load series from the cached OPSD file.

Steps: verify the cached raw file against its manifest and the declared SHA-256,
map ``utc_timestamp → timestamp_utc`` and ``CZ_load_actual_entsoe_transparency →
load_mw``, check UTC/CET-CEST alignment, audit coverage and quality, then write

* ``<output_dir>/opsd_2020-10-06_cz_load.parquet``: the typed series, trimmed to
  the observed span, interior missing values kept as NaN (written only when the
  series is processable and the alignment check passes);
* ``<output_dir>/opsd_2020-10-06_cz_load.audit.json``: the provenance and audit
  record (always written).

No network access is needed.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from peakwindow import __version__, opsd
from peakwindow.acquisition import AcquisitionError, read_manifest, sha256_of, verify_cached
from peakwindow.audit import AuditReport, audit_load_series
from peakwindow.contracts import LOAD_COLUMN, TIMESTAMP_COLUMN
from peakwindow.csv_adapter import CsvReadError

RECORD_SCHEMA_VERSION = "1"
OUTPUT_STEM = f"opsd_{opsd.RELEASE}_cz_load"


class PreparationError(Exception):
    """Raised when the cached source cannot be verified or outputs cannot be written."""


@dataclass(frozen=True)
class PreparationResult:
    audit: AuditReport
    alignment: opsd.AlignmentCheck
    record_path: Path
    processed_path: Path | None

    @property
    def succeeded(self) -> bool:
        return self.processed_path is not None


def output_paths(output_dir: Path) -> tuple[Path, Path]:
    """Return ``(parquet_path, record_path)`` under ``output_dir``."""
    return output_dir / f"{OUTPUT_STEM}.parquet", output_dir / f"{OUTPUT_STEM}.audit.json"


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def prepare_opsd(data_dir: Path, output_dir: Path) -> PreparationResult:
    raw = opsd.raw_path(data_dir)
    try:
        manifest = read_manifest(raw)
        raw_sha256 = verify_cached(raw, opsd.SOURCE)
    except AcquisitionError as exc:
        raise PreparationError(f"{exc}. Run `peakwindow fetch-opsd` first.") from exc

    try:
        frame, alignment = opsd.read_czech_load(raw)
    except CsvReadError as exc:
        raise PreparationError(str(exc)) from exc
    audit, series = audit_load_series(frame, source=f"{opsd.FILE_NAME}:{opsd.CZ_LOAD_COLUMN}")

    parquet_path, record_path = output_paths(output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        processed: dict[str, Any] | None = None
        if series is not None and alignment.passed:
            temporary = parquet_path.with_name(parquet_path.name + ".tmp")
            series.to_parquet(temporary, index=False)
            os.replace(temporary, parquet_path)
            processed = {
                "file_name": parquet_path.name,
                "sha256": sha256_of(parquet_path),
                "rows": len(series),
                "columns": {TIMESTAMP_COLUMN: "timestamp[UTC]", LOAD_COLUMN: "float64 (MW)"},
                "rule": "trimmed to the observed span; interior missing load kept as NaN; "
                "no filling, interpolation or outlier removal",
            }
        else:
            parquet_path.unlink(missing_ok=True)  # never leave a stale series behind

        record = {
            "schema_version": RECORD_SCHEMA_VERSION,
            "prepared_at_utc": _utc_now(),
            "peakwindow_version": __version__,
            "source": {
                "release": opsd.RELEASE,
                "doi": opsd.DOI_URL,
                "url": opsd.SOURCE.url,
                "citation": opsd.CITATION,
            },
            "raw": {
                "file_name": raw.name,
                "sha256": raw_sha256,
                "sha256_matches_declared": raw_sha256 == opsd.SOURCE.sha256,
                "size_bytes": raw.stat().st_size,
                "acquired_at_utc": manifest.get("acquired_at_utc"),
            },
            "column_mapping": {
                opsd.UTC_COLUMN: TIMESTAMP_COLUMN,
                opsd.CZ_LOAD_COLUMN: LOAD_COLUMN,
            },
            "alignment": {**asdict(alignment), "passed": alignment.passed},
            "processed": processed,
            "audit": audit.to_dict(),
        }
        record_path.write_text(
            json.dumps(record, indent=2, allow_nan=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise PreparationError(f"cannot write outputs to {output_dir}: {exc}") from exc

    return PreparationResult(
        audit=audit,
        alignment=alignment,
        record_path=record_path,
        processed_path=parquet_path if processed is not None else None,
    )
