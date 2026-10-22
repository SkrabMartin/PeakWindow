# Architecture

Every module below exists and is tested. Nothing is a placeholder for future work.

## Dependency direction

```text
cli.py
 ├─ validate-data ──► csv_adapter.py, validation.py
 ├─ fetch-opsd ─────► acquisition.py (the only network code), opsd.py (source definition)
 ├─ prepare-opsd ───► prepare.py
 │                      ├─► acquisition.py   verify the cached file, read its manifest
 │                      ├─► opsd.py          map columns, check UTC vs CET/CEST alignment
 │                      └─► audit.py ──► validation.py ──► contracts.py
 ├─ evaluate-baselines ─► prepare.load_processed (SHA-256 check)
 │                        └─► evaluation.py
 │                              ├─► splits.py      frozen origins, holdout lock
 │                              ├─► features.py ─► contracts.select_history, holidays.py
 │                              └─► baselines.py
 └─ select-model / evaluate-holdout ─► study.py ─► evaluation.py, models.py (Ridge)

tests/ ── call every module directly; test_cli.py and test_prepare.py also drive main().
          No test uses the network: downloads are tested with file:// URLs.
```

- **`contracts.py`**: canonical columns (`timestamp_utc`, `load_mw`), the 24-hour
  horizon, `ForecastRequest`, `target_timestamps()` and `select_history()`. No I/O.
- **`validation.py`**: `validate_hourly_frame()` → `QualityReport`. It is pure: no
  filesystem, no printing, and the input is never modified.
- **`audit.py`**: `audit_load_series()` → `AuditReport` plus an optional typed
  series. It adds the observed span, missing-load runs, yearly coverage, descriptive
  statistics and the largest hourly changes. It is pure, and nothing is filled or removed.
- **`csv_adapter.py`**: reads the canonical fixture schema as raw text.
- **`opsd.py`**: the OPSD release definition (URL, declared size and SHA-256, citation,
  column names), `read_czech_load()`, which maps the two source columns to the
  canonical schema as raw text, and `check_alignment()` (UTC vs CET/CEST).
- **`acquisition.py`**: `fetch()` downloads to a `.part` file, verifies the size and
  SHA-256, moves the file into place and writes the provenance manifest.
  `verify_cached()` re-checks a cached copy. The standard library's `urllib` is the
  only network code in the package.
- **`prepare.py`**: the offline pipeline behind `prepare-opsd`. It verifies the cache,
  then reads, aligns, audits, and writes the Parquet series and the JSON audit record.
  `load_processed()` reads the series back only if its SHA-256 matches that record.
- **`splits.py`**: the frozen split `split-v1`, with origin dates per period.
- **`features.py`**: `build_features()` returns 24 rows per origin: the Prague
  calendar of each target plus lag features. It applies `select_history()` first,
  so it cannot see the origin or anything later.
- **`baselines.py`**: the daily and weekly seasonal naive, read from lag features.
- **`holidays.py`**: the Czech public-holiday calendar (fixed dates and the Easter computus).
- **`models.py`**: `RidgeForecaster`, one standardized Ridge per horizon, with a JSON
  export of its scaler and coefficients.
- **`study.py`**: `select_model()` (fit on train, choose by validation MAE) and
  `final_evaluation()` (refit on train + validation, score the holdout once, plus the
  pre-declared sensitivity view).
- **`evaluation.py`**: `build_dataset()` and `evaluate()`. They score every model on
  the same origins, record data-gap exceptions, compute metrics and enforce the
  holdout lock. Each origin's features are built from a window that ends at the origin.
  `actual_values()` is the only code that reads target values, and it is used only
  for scoring.
- **`cli.py`**: argparse front end. It owns all terminal output and exit codes
  (0 success, 1 data failure, 2 usage, I/O, network or checksum error).
- **`__main__.py`** supports `python -m peakwindow`. **`__init__.py`** exposes
  `__version__` only. Importing the package has no side effects.

## Design rules

1. Core logic (contracts, validation, audit) never touches the filesystem or the
   terminal.
2. Validation and audit measure and report. They never repair, sort, deduplicate,
   fill, interpolate or remove data.
3. Only `fetch-opsd` uses the network, and only when run explicitly. Imports and
   `--help` download, write and start nothing.
4. Downloads and derived files are verified by SHA-256 and recorded in manifests,
   and they live in git-ignored directories.
5. Runtime dependencies are pandas, NumPy, scikit-learn (Ridge), pyarrow (Parquet)
   and tzdata (time zones on Windows).

## Planned growth (not implemented)

Later phases will add uncertainty, backtesting, reporting and a dashboard. They will reuse `features.py`, `splits.py` and `evaluation.py` rather than
redefine them. See [roadmap.md](roadmap.md).
