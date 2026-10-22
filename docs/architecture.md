# Architecture (Phase 0)

Phase 0 is a deliberately small package. Every module below exists and is tested;
nothing is a placeholder for future work.

## Dependency direction

```text
cli.py ──► csv_adapter.py          (filesystem: read CSV as raw text)
   │
   └─────► validation.py ──► contracts.py

tests/ ── call contracts, validation and csv_adapter functions directly;
          test_cli.py also drives main(), `python -m peakwindow` and the console script
```

- **`contracts.py`**: canonical column names (`timestamp_utc`, `load_mw`), the
  24-hour horizon, `ForecastRequest` (00:00 UTC origin, fixed horizon),
  `target_timestamps()` and `select_history()`. No I/O. Depends only on pandas.
- **`validation.py`**: `validate_hourly_frame()` → `QualityReport`. A pure function
  of a DataFrame: no filesystem access, no printing, and the input is never modified.
  Serialization (`to_dict`, `to_json`) produces standard JSON only (`allow_nan=False`).
- **`csv_adapter.py`**: `read_canonical_csv()` reads the canonical fixture schema
  with every cell kept as text, and turns structural problems (missing file, empty
  file, malformed CSV, bad encoding) into `CsvReadError`. It is **not** an OPSD parser.
- **`cli.py`**: argparse front end. It owns all presentation: status lines on
  stderr, JSON on stdout or to `--output`, and exit codes (0 pass, 1 quality
  failure, 2 usage or I/O error).
- **`__main__.py`** supports `python -m peakwindow`. **`__init__.py`** exposes
  `__version__` only. Importing the package has no side effects.

## Design rules

1. Core logic (contracts, validation) never touches the filesystem or the terminal,
   so later phases (ingestion, dashboard) can reuse it unchanged.
2. Validation measures and reports. It never repairs, sorts, deduplicates, fills or
   imputes data.
3. Neither imports nor `--help` download, write, train or start anything.
4. Production dependencies are pandas only. CLI, dataclasses, JSON and paths use
   the standard library.

## Planned growth (not implemented)

Later phases are expected to add modules for OPSD ingestion, features, baselines,
models, uncertainty, backtesting and reporting, plus a dashboard. They will depend
on `contracts.py` rather than redefine time semantics. See [roadmap.md](roadmap.md).
