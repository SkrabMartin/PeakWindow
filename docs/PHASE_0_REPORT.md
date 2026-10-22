# Phase 0 completion report: Foundations

**Date:** 2026-10-22 · **Author:** Martin Škrabana ·
**Status:** complete locally. The push and CI outcome for the delivering commit are
reported in the delivery message, not here.

> Every result in this phase comes from **synthetic** data or hand-built test frames.
> No real electricity data was downloaded, and no forecast or accuracy figure exists.

## 1. Scope delivered

- An installable `src/` package, `peakwindow` 0.1.0, built with hatchling and
  dependency-locked with uv (`uv.lock` is committed).
- Typed contracts (`contracts.py`): the canonical columns `timestamp_utc` and `load_mw`,
  a frozen `ForecastRequest`, `target_timestamps()` and `select_history()`.
- A pure data-quality validator (`validation.py`) that returns a versioned,
  JSON-serializable `QualityReport`.
- A canonical-schema CSV adapter (`csv_adapter.py`). It is explicitly *not* the OPSD parser.
- A CLI: the `peakwindow` console script, `python -m peakwindow`, and the
  `validate-data` command.
- A deterministic 240-hour synthetic fixture with a documented, test-checked generation rule.
- 68 pytest tests, Ruff lint and format, strict mypy, and a GitHub Actions workflow.
- Docs: README, architecture, methodology, data sources, roadmap, this report, and the
  original brief preserved verbatim as `docs/project_brief.md`.

## 2. Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Runtime | Python 3.12 only (`requires-python >=3.12`, `.python-version` = 3.12) | This is the only version tested locally (3.12.13) and in CI. Nothing newer is claimed. |
| Packaging | `pyproject.toml` + hatchling, uv 0.10.11, `uv.lock` | A standard backend with a reproducible frozen install. |
| Runtime dependencies | `pandas>=3.0` (locked 3.1.0) only | Everything else uses the standard library (argparse, dataclasses, json, pathlib). |
| Dev dependencies | pytest 9.1.1, ruff 0.17.1, mypy 2.4.0, pandas-stubs | No modeling or UI stack before it is used. |
| Offset-aware timestamps | Normalized to UTC | They are unambiguous instants. |
| Naive timestamps | Rejected | Guessing a timezone could silently shift data by an hour. |
| Timestamp parsing | `datetime.fromisoformat` (strict ISO 8601) | Predictable and documented, with no locale guessing. |
| Ordering rule | Valid timestamps must be non-decreasing in row order | Duplicates are reported separately. |
| Load "missing" | null, empty, whitespace, or NaN in any spelling | The CSV `"NaN"` and an in-memory `float('nan')` are treated consistently. |
| Zero load | Allowed and counted as informational | No source rule justifies rejecting it. |
| Provenance flag | `--synthetic` sets `synthetic: true`. Otherwise it is `null` (undeclared) | The tool never infers provenance. |
| Report output | JSON goes to stdout, status to stderr | stdout stays pipeable. |
| Exit codes | 0 pass, 1 quality failure, 2 usage or I/O error | Callers can tell "bad data" apart from "could not run". |
| License | MIT for original code, © Martin Škrabana | Third-party data is explicitly excluded. |
| Module added beyond the starting layout | `csv_adapter.py` | Keeps filesystem access out of the pure validator. |
| CI actions | `actions/checkout@v7` and `astral-sh/setup-uv@v10.3.0` (exact pin) | These were the latest releases on 2026-10-22. setup-uv publishes no floating `v10` tag, so the exact release is pinned. v10.3.0 dates from 2026-10-09, which avoids the releases from the previous two days. |

## 3. Final architecture and CLI behavior

`cli → csv_adapter / validation → contracts`. See [architecture.md](architecture.md).

`peakwindow validate-data --input PATH [--output PATH] [--synthetic]`:

- Prints one status line (`PASSED` or `FAILED`, with a `[SYNTHETIC]` tag when declared)
  and its issues to stderr.
- Without `--output`, the JSON report goes to stdout. With `--output`, parent
  directories are created and only that file is written.
- A missing file, a directory, an empty file, malformed CSV or non-UTF-8 input
  produces a one-line error and exit 2, with no traceback.
- Missing columns, empty data rows and any quality issue produce a failed report and exit 1.
- `--help` and `--version` write nothing. Importing the package has no side effects.

The report fields (schema version `"1"`) are: `source`, `synthetic`, `total_rows`,
`valid_timestamp_count`, `first_timestamp_utc`, `last_timestamp_utc`,
`invalid_timestamp_count`, `naive_timestamp_count`, `off_grid_timestamp_count`,
`duplicate_timestamp_count`, `valid_timestamps_ordered`, `missing_hour_count`,
`gap_ranges`, `missing_load_count`, `nonnumeric_load_count`, `nonfinite_load_count`,
`negative_load_count`, `zero_load_count`, `passed` and `issues`. Serialization uses
`allow_nan=False`.

## 4. Verification (observed locally, Windows 11, Python 3.12.13, uv 0.10.11)

After `uv sync --frozen --group dev`, every command below was run with `--offline`
as well, so the checks do not need the network:

| Command | Result |
|---|---|
| `uv run --frozen ruff check .` | All checks passed |
| `uv run --frozen ruff format --check .` | 17 files already formatted |
| `uv run --frozen mypy src/peakwindow` | Success: no issues found in 6 source files (strict) |
| `uv run --frozen pytest` | **68 passed** |
| `uv run --frozen peakwindow --help` | exit 0 |
| `uv run --frozen python -m peakwindow --help` | exit 0 |
| `uv run --frozen peakwindow validate-data --input data/fixtures/synthetic_hourly_load.csv --synthetic` | `PASSED [SYNTHETIC]`, 240 rows, 0 issues, exit 0 |
| `... --output artifacts/fixture-quality.json` | Valid JSON written, `passed: true`, exit 0 |
| `uv build` (into a temporary directory) | The wheel contains the 6 modules, `py.typed` and the LICENSE |

Test breakdown: `test_contracts.py` 31, `test_validation.py` 25, `test_cli.py` 12.

**Sensitivity check:** temporarily changing `select_history` from `<` to `<=` made
7 contract tests fail (exact-origin value, duplicate origin row, scrambled future,
dropped future and others). The boundary tests are therefore not vacuous. The change
was reverted.

What the tests cover:

- The fixture matches its documented generation rule and passes with full coverage
  and no gaps.
- Duplicates (counted as extras, unable to hide gaps), disorder (reported, not
  sorted), explicit gap ranges, and invalid, naive and off-grid timestamps.
- Offset normalization, including duplicates hidden by different offsets.
- The Europe/Prague DST days 2019-03-31 (23 local hours) and 2019-10-27 (25 local
  hours) validate as unique, gap-free UTC series.
- Missing, nonnumeric, NaN, infinite, negative and zero load rules. Missing columns
  and empty input fail. The JSON is standard and its field set stable.
- Exactly 24 ordered targets, plus rejection of invalid origins and horizons.
- History selection excludes the origin, and its result does not change when values
  at the origin, at the last target or beyond are mutated, or when future rows are
  added, removed or duplicated.
- CLI help, stdout and file output, exit codes 0, 1 and 2, missing, empty and
  unwritable files, and runs from a directory outside the repository with an
  absolute path, through both `python -m` and the installed console script.

## 5. Offline guarantees and data identity

- Tests and CLI use no network, downloads, secrets or paid services.
- The only bundled data is `data/fixtures/synthetic_hourly_load.csv`. It is
  **synthetic**, contains no OPSD or ENTSO-E measurements, and is labeled as such in
  its README and in CLI output when `--synthetic` is passed.
- Generated outputs (`artifacts/` and others) are git-ignored.

## 6. Limitations and deferred work

- No ingestion, preprocessing, features, baselines, models, calibration, backtests,
  dashboard, Docker, API or deployment. These are deliberately deferred (see the
  [roadmap](roadmap.md)).
- The tests prove the history-selection boundary only, not leakage safety of a
  feature builder, which does not exist yet.
- Immediate availability of pre-origin observations is assumed. Publication delays
  have not been audited.
- Only Python 3.12 is tested.

## 7. Blockers and source verification

- **Dependencies and network:** none. Python 3.12.13 was installed with uv, and
  dependencies were resolved and locked from PyPI during setup.
- **OPSD metadata:** `datapackage.json` was fetched and confirms the file, both
  field names, the units and the "start of timeperiod" semantics. See
  [data_sources.md](data_sources.md).
- **Still unverified:** the actual coverage and gaps of the Czech series, the literal
  timestamp text in the CSV, the hash algorithm, and the license terms (the metadata
  has no license field).
- **Remote CI:** the status for the pushed commit is reported separately in the delivery message.

## 8. Recommended Phase 1 scope

1. Add an explicit `peakwindow fetch-opsd` command (opt-in, never run by tests) that
   downloads `time_series_60min_singleindex.csv` into git-ignored `data/raw/`.
2. Write a provenance manifest with the URL, release, DOI, acquisition time (UTC),
   size and SHA-256, and compare it with the declared hash.
3. Add an OPSD adapter that maps `utc_timestamp → timestamp_utc` and
   `CZ_load_actual_entsoe_transparency → load_mw` and handles the real timestamp
   format, with tests built on a tiny excerpt-shaped fixture.
4. Cache the processed series as Parquet, with a rebuild path keyed to the manifest.
5. Run the Phase 0 validator on the real series and publish an **actual coverage
   and gap audit**: first and last valid hours, missing runs, and DST behavior.
6. Review OPSD and ENTSO-E terms, and verify interval alignment against `cet_cest_timestamp`.
7. Propose split boundaries only after the audit.
