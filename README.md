# PeakWindow: Electricity Demand Forecasting Laboratory

PeakWindow is an independent portfolio project in **Data Science** (primary) and
**Machine Learning Engineering** (secondary). Its eventual goal is honest,
reproducible **next-24-hour forecasting of historical Czech national electricity
demand**, with meaningful baselines, strictly chronological evaluation, calibrated
uncertainty and an interactive historical dashboard. It runs locally on a CPU with
no paid services.

> **Status: work in progress. Phase 3 (Ridge model and final evaluation) is done.**
> The point forecasts have been evaluated once on the 2020 holdout, following a
> protocol committed before the holdout was opened. Prediction intervals, backtests
> and the dashboard are still to come. PeakWindow is a historical research
> laboratory, not a live forecasting feed and not a grid-control system.

## What works now vs. later

| Works now (Phases 0–3) | Later phases |
|---|---|
| Installable `src/` package with a `peakwindow` CLI and `python -m peakwindow` | |
| Typed contracts: canonical hourly series, 00:00 UTC forecast request, 24 targets, strictly pre-origin history | Calibrated prediction intervals and rolling-origin backtests (Phase 4) |
| `fetch-opsd` / `prepare-opsd`: verified OPSD download, provenance manifest, alignment check, coverage audit, Parquet series | Historical Streamlit dashboard, optional packaging and deployment (Phase 5) |
| Leakage-safe calendar and lag features, with future-mutation leakage tests | |
| Frozen chronological split `split-v1` with a holdout locked in code | |
| `evaluate-baselines`: daily and weekly seasonal naive, MAE, RMSE, nMAE, relative MAE, error quantiles, experiment records | |
| Czech public-holiday calendar and holiday features | |
| `select-model`: per-horizon Ridge, 12 candidates fitted on train and chosen by validation MAE | |
| `evaluate-holdout --confirm`: one final evaluation following a pre-registered protocol | |
| Synthetic fixtures, an offline test suite, Ruff, strict mypy, GitHub Actions | |

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) (developed with uv 0.10.11) and
Python **3.12**, which is the only tested version. uv can install it with
`uv python install 3.12`.

```bash
git clone https://github.com/SkrabMartin/PeakWindow.git
cd PeakWindow
uv sync --frozen --group dev
```

Network access is needed only for installing dependencies and for the explicit
`fetch-opsd` download. Everything else, including all tests, runs offline.

## CLI

```bash
uv run --frozen peakwindow --help
uv run --frozen python -m peakwindow --help

# Print the JSON quality report to stdout (status line goes to stderr)
uv run --frozen peakwindow validate-data --input data/fixtures/synthetic_hourly_load.csv --synthetic

# Write the report to a file instead (parent directories are created)
uv run --frozen peakwindow validate-data --input data/fixtures/synthetic_hourly_load.csv --synthetic --output artifacts/fixture-quality.json
```

`validate-data` expects a CSV with the columns `timestamp_utc` (ISO 8601 with an
explicit offset) and `load_mw`. `--synthetic` records in the report that the input
is synthetic. Without it, provenance is recorded as `null` (undeclared). PeakWindow
does not guess.

### Real data (OPSD)

```bash
# One-time download (~130 MB) into data/raw/, verified by SHA-256, with a manifest
uv run --frozen peakwindow fetch-opsd

# Offline: extract and audit the Czech series, write data/processed/
uv run --frozen peakwindow prepare-opsd
```

`prepare-opsd` writes `data/processed/opsd_2020-10-06_cz_load.parquet` (columns
`timestamp_utc`, `load_mw`; missing hours kept as NaN) and
`opsd_2020-10-06_cz_load.audit.json` (provenance, alignment and audit). Both
commands accept `--data-dir`. A cached download is re-verified and reused, and
`fetch-opsd --force` downloads again.

| Exit status | Meaning |
|---|---|
### Baselines

```bash
# Score the seasonal naive baselines on one period of the frozen split
uv run --frozen peakwindow evaluate-baselines --period validation
uv run --frozen peakwindow evaluate-baselines --period train
```

This writes `experiments/baselines_<period>.json` with the split version, the
SHA-256 of the processed data, the configuration, the data-gap exceptions and the
metrics. Allowed periods are `train`, `validation` and `calibration`. The final
`holdout` is locked.

### Ridge model and final evaluation

```bash
# Fit 12 Ridge candidates on train, choose by validation MAE (holdout untouched)
uv run --frozen peakwindow select-model

# FINAL: refit the chosen model on train + validation and score the 2020 holdout
uv run --frozen peakwindow evaluate-holdout --confirm
```

`select-model` writes `experiments/model_selection.json`. `evaluate-holdout` reads
that record, refuses to run if the processed data changed in the meantime, and writes
`experiments/final_holdout.json` plus the fitted model as JSON coefficients
(`ridge_final_model.json`, with its SHA-256 in the record).

| Exit status | Meaning |
|---|---|
| `0` | Success (`validate-data`: every check passed; `prepare-opsd`: series written; `evaluate-baselines`: record written) |
| `1` | Data was read but failed (`validate-data`: a quality check; `prepare-opsd`: series not processable or alignment failed) |
| `2` | Usage error, network or checksum failure, a locked holdout, or a file that could not be read or written |

## Verification

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy src/peakwindow
uv run --frozen pytest
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same checks,
plus a CLI smoke test, on Python 3.12 for every push and pull request.

## Repository layout

```text
PeakWindow/
├── .github/
│   └── workflows/
│       └── ci.yml
├── data/
│   ├── README.md
│   └── fixtures/
│       ├── README.md
│       └── synthetic_hourly_load.csv
├── docs/
│   ├── architecture.md
│   ├── data_sources.md
│   ├── methodology.md
│   ├── project_brief.md
│   └── roadmap.md
├── src/
│   └── peakwindow/
│       ├── __init__.py
│       ├── __main__.py
│       ├── acquisition.py
│       ├── audit.py
│       ├── baselines.py
│       ├── cli.py
│       ├── contracts.py
│       ├── csv_adapter.py
│       ├── evaluation.py
│       ├── features.py
│       ├── holidays.py
│       ├── models.py
│       ├── opsd.py
│       ├── prepare.py
│       ├── py.typed
│       ├── splits.py
│       ├── study.py
│       └── validation.py
├── tests/
│   ├── conftest.py
│   ├── test_acquisition.py
│   ├── test_audit.py
│   ├── test_cli.py
│   ├── test_contracts.py
│   ├── test_evaluation.py
│   ├── test_features.py
│   ├── test_holidays.py
│   ├── test_models.py
│   ├── test_opsd.py
│   ├── test_prepare.py
│   ├── test_study.py
│   └── test_validation.py
├── .gitignore
├── .python-version
├── LICENSE
├── README.md
├── pyproject.toml
└── uv.lock
```

- `src/peakwindow/contracts.py` defines the canonical columns, `ForecastRequest`,
  `target_timestamps()` and `select_history()`. It contains no I/O.
- `src/peakwindow/validation.py` holds the pure data-quality validator and the
  JSON-serializable `QualityReport`.
- `src/peakwindow/audit.py` adds coverage auditing on top of validation: observed span,
  missing runs, yearly coverage and suspicious changes.
- `src/peakwindow/csv_adapter.py` reads the canonical fixture schema as raw text.
- `src/peakwindow/opsd.py` defines the OPSD source and maps its Czech columns. It also
  checks UTC vs CET/CEST alignment.
- `src/peakwindow/acquisition.py` handles verified downloads and provenance manifests.
- `src/peakwindow/prepare.py` is the offline pipeline behind `prepare-opsd`. It also
  loads the processed series after verifying its SHA-256.
- `src/peakwindow/splits.py` defines the frozen chronological split `split-v1`.
- `src/peakwindow/features.py` is the leakage-safe feature builder (24 rows per origin).
- `src/peakwindow/baselines.py` holds the daily and weekly seasonal naive baselines.
- `src/peakwindow/evaluation.py` builds datasets per period, applies the data-gap
  exceptions and the holdout lock, and computes the metrics for any model.
- `src/peakwindow/holidays.py` is the Czech public-holiday calendar.
- `src/peakwindow/models.py` is the per-horizon Ridge forecaster, with JSON export.
- `src/peakwindow/study.py` runs model selection on validation and the final holdout
  evaluation.
- `src/peakwindow/cli.py` and `__main__.py` provide the command-line front end:
  presentation, output files and exit codes.
- `tests/` contains boundary, quality, audit, acquisition, leakage, model, study and
  CLI tests. They use only synthetic data and `file://` URLs.
- `data/fixtures/` holds the synthetic fixture and its generation rule.
- `docs/` holds the methodology, architecture, data provenance, roadmap and the
  original project brief.

## Time semantics

- **UTC everywhere.** `timestamp_utc` is timezone-aware UTC on an exact hour and labels
  the **start** of its hour. Offset-aware inputs are normalized to UTC. Naive
  timestamps are rejected. Europe/Prague local time is for display only.
- **Forecast origin** is exactly **00:00 UTC**. The **horizon is fixed at 24**, and the
  targets are `origin + 0h … origin + 23h`.
- **Inputs are strictly pre-origin:** `timestamp_utc < origin`. The value at the
  origin is a target, never an input.
- **Units:** load is in **MW**.
- **Availability assumption:** an observation before the origin is assumed to have
  been published before issuance. Real publication delays are an open, unaudited
  limitation.
- **Leakage tests** check that features stay identical when any data at or after the
  origin is changed, removed or set to NaN. The same test catches a deliberately
  leaky builder.

Details: [docs/methodology.md](docs/methodology.md).

## Data

- **Fixture:** `data/fixtures/synthetic_hourly_load.csv` is **synthetic**. It has
  240 deterministic hourly rows (2019-03-25 to 2019-04-03 UTC) and contains no real
  measurements. Its generation rule is documented in
  [data/fixtures/README.md](data/fixtures/README.md) and checked by a test.
- **Real source (downloaded, never committed):** Open Power System Data, Time series
  release 2020-10-06, DOI [10.25832/time_series/2020-10-06](https://doi.org/10.25832/time_series/2020-10-06),
  file `time_series_60min_singleindex.csv`, columns `utc_timestamp` and
  `CZ_load_actual_entsoe_transparency`.
- **Audited Czech coverage:** `2015-01-01T01:00Z` to `2020-09-30T22:00Z` (50,398
  hours). 107 hours are missing, in two runs (104 h in November 2016 and 3 h in
  November 2017). UTC and CET/CEST labels agree on every row. A few isolated spikes
  are documented and kept unchanged. Details are in
  [docs/data_sources.md](docs/data_sources.md).
- **Attribution boundary:** the MIT license below covers PeakWindow's code and docs
  only. OPSD and ENTSO-E data keep their own terms.
- **Generated files** (`artifacts/`, `experiments/`, `data/raw/`, `data/processed/`,
  `*.parquet`) are git-ignored and never committed.

## Final holdout results (2020-01-01 … 2020-09-29)

Measured **once** on 273 origins × 24 hours, with Ridge refitted on train + validation.
The [protocol](docs/methodology.md#final-evaluation-protocol-pre-registered-before-opening-the-holdout)
was committed (`ec20425`) before the holdout was opened.

| Model | MAE (MW) | RMSE (MW) | nMAE | Relative MAE |
|---|---|---|---|---|
| Seasonal naive, daily (`t − 24 h`) | 446.6 | 678.7 | 6.26 % | 1.549 |
| Seasonal naive, weekly (`t − 168 h`) | 288.3 | 486.6 | 4.04 % | 1.000 |
| **Ridge, per horizon** | **133.7** | **179.7** | **1.87 %** | **0.464** |

Ridge beats the weekly naive both inside the COVID-19 window (April–June 2020:
140.3 vs 284.7 MW) and outside it (130.3 vs 290.1 MW). Public holidays remain its
largest errors.

## Validation results (2019-01-01 … 2019-06-30)

Measured on 181 origins × 24 hours. This is the model-selection period, **not** the
final holdout.

| Model | MAE (MW) | RMSE (MW) | nMAE | Relative MAE |
|---|---|---|---|---|
| Seasonal naive, daily (`t − 24 h`) | 510.1 | 754.9 | 6.54 % | 1.342 |
| Seasonal naive, weekly (`t − 168 h`) | 380.2 | 622.9 | 4.88 % | 1.000 |
| **Ridge, per horizon (selected)** | **134.2** | **191.8** | **1.72 %** | **0.353** |

The large improvement was checked for leakage. Destroying all data from the origin
onward leaves the predictions unchanged, and the error grows with the horizon as
expected. Details, ablations and the pre-registered final protocol are in
[docs/methodology.md](docs/methodology.md).

## Roadmap

1. ~~Ingestion and quality audit of the real OPSD Czech series.~~ Done.
2. ~~Leakage-safe features and daily and weekly seasonal naive baselines.~~ Done.
3. ~~Ridge and chronological evaluation with a single, pre-registered holdout run.~~ Done.
4. Calibrated prediction intervals and rolling-origin backtests.
5. Historical dashboard and optional packaging.

See [docs/roadmap.md](docs/roadmap.md) and [docs/architecture.md](docs/architecture.md).
The original design brief is
in [docs/project_brief.md](docs/project_brief.md). It describes the eventual product,
not current features.

## Limitations

- The forecasts are point forecasts only. Prediction intervals come in Phase 4.
- Only linear models (Ridge) have been tried. Gradient boosting is optional future work.
- OPSD states no data license (ENTSO-E terms apply), so each user downloads the
  data and this repository never redistributes it.
- The final holdout period (2020) includes the COVID-19 demand drop, which made April
  and May about 11–13% lower than in 2019. The results are reported with and without it.
- There is one test period (nine months of 2020) for one country. Rolling-origin
  backtests are planned for Phase 4.
- No live feed. This is a historical reconstruction laboratory, and its output must
  never be read as operational guidance.
- Publication delays of source measurements have not been audited.
- Only Python 3.12 is tested. Local development ran on Windows and CI runs on Ubuntu.

## License and contributing

Original code and documentation: [MIT](LICENSE) © Martin Škrabana, original
project author. Third-party datasets are not relicensed.

Before opening a pull request, run the four verification commands above. Do not
commit downloaded data, generated artifacts or credentials.
