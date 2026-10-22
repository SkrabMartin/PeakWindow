"""Read a CSV in PeakWindow's canonical fixture schema (``timestamp_utc``, ``load_mw``).

This adapter exists for small fixtures and hand-made test files. It is **not** the
production OPSD parser: it performs no column mapping, no source-specific parsing
and no type coercion. Every cell is read as raw text so that the validator can
classify it without pandas silently converting or dropping values.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class CsvReadError(Exception):
    """Raised when a CSV file cannot be read as a table at all."""


def read_canonical_csv(path: Path) -> pd.DataFrame:
    """Read ``path`` as UTF-8 CSV with every cell kept as an unmodified string."""
    if not path.exists():
        raise CsvReadError(f"input file not found: {path}")
    if not path.is_file():
        raise CsvReadError(f"input path is not a file: {path}")
    try:
        return pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            encoding="utf-8",
        )
    except pd.errors.EmptyDataError as exc:
        raise CsvReadError(f"input file is empty (no header row): {path}") from exc
    except pd.errors.ParserError as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "parse error"
        raise CsvReadError(f"input file is not well-formed CSV: {path}: {reason}") from exc
    except UnicodeDecodeError as exc:
        raise CsvReadError(f"input file is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise CsvReadError(f"cannot read input file {path}: {exc.strerror or exc}") from exc
