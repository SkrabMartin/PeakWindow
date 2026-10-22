# PeakWindow: Electricity Demand Forecasting Laboratory

PeakWindow is an independent portfolio project in **Data Science** (primary) and
**Machine Learning Engineering** (secondary). Its eventual goal is honest,
reproducible **next-24-hour forecasting of historical Czech national electricity
demand**, with meaningful baselines, strictly chronological evaluation, calibrated
uncertainty and an interactive historical dashboard. It runs locally on a CPU with
no paid services.

> **Status: work in progress. Phase 1 (data ingestion and audit) is done.**
> The real Czech load series can be downloaded, verified and audited, but there are
> **no trained models, no forecasts and no measured predictive results yet**.
> PeakWindow is a historical research laboratory, not a live forecasting feed and not
> a grid-control system.

## What works now vs. later

| Works now (Phases 0–1) | Later phases |
|---|---|
| Installable `src/` package with a `peakwindow` CLI and `python -m peakwindow` | Leakage-safe features and seasonal naive baselines (Phase 2) |
| Typed contracts: canonical hourly series, 00:00 UTC forecast request, 24 targets, strictly pre-origin history | Ridge (optionally gradient boosting) and chronological evaluation (Phase 3) |
| Offline data-quality validator with a JSON report | Calibrated prediction intervals and rolling-origin backtests (Phase 4) |
| `fetch-opsd`: verified download (SHA-256, size) with a provenance manifest | Historical Streamlit dashboard, optional packaging and deployment (Phase 5) |
| `prepare-opsd`: offline Czech series extraction, UTC/CET alignment check, coverage audit, Parquet output | |
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
| `0` | Success (`validate-data`: every check passed; `prepare-opsd`: series written) |
| `1` | Data was read but failed (`validate-data`: a quality check; `prepare-opsd`: series not processable or alignment failed) |
| `2` | Usage error, network or checksum failure, or a file that could not be read or written |

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
│       ├── cli.py
│       ├── contracts.py
│       ├── csv_adapter.py
│       ├── opsd.py
│       ├── prepare.py
│       ├── py.typed
│       └── validation.py
├── tests/
│   ├── conftest.py
│   ├── test_acquisition.py
│   ├── test_audit.py
│   ├── test_cli.py
│   ├── test_contracts.py
│   ├── test_opsd.py
│   ├── test_prepare.py
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
- `src/peakwindow/prepare.py` is the offline pipeline behind `prepare-opsd`.
- `src/peakwindow/cli.py` and `__main__.py` provide the command-line front end:
  presentation, output files and exit codes.
- `tests/` contains boundary, quality, audit, acquisition and CLI tests. They use only
  synthetic data and `file://` URLs.
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
- The tests establish the history-selection boundary only. Leakage safety of real
  features must be tested again once a feature builder exists (Phase 2).

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

## Roadmap

1. ~~Ingestion and quality audit of the real OPSD Czech series.~~ Done.
2. Leakage-safe features and daily and weekly seasonal naive baselines.
3. Ridge and chronological evaluation (MAE and RMSE in MW, normalized MAE).
4. Calibrated prediction intervals and rolling-origin backtests.
5. Historical dashboard and optional packaging.

See [docs/roadmap.md](docs/roadmap.md) and [docs/architecture.md](docs/architecture.md).
The original design brief is
in [docs/project_brief.md](docs/project_brief.md). It describes the eventual product,
not current features.

## Limitations

- No trained models, no forecasts and no measured predictive accuracy.
- OPSD states no data license (ENTSO-E terms apply), so each user downloads the
  data and this repository never redistributes it.
- The final holdout period (2020) includes the COVID-19 demand drop, which made April
  and May about 11–13% lower than in 2019.
- No live feed. This is a historical reconstruction laboratory, and its output must
  never be read as operational guidance.
- Publication delays of source measurements have not been audited.
- Only Python 3.12 is tested. Local development ran on Windows and CI runs on Ubuntu.

## License and contributing

Original code and documentation: [MIT](LICENSE) © Martin Škrabana, original
project author. Third-party datasets are not relicensed.

Before opening a pull request, run the four verification commands above. Do not
commit downloaded data, generated artifacts or credentials.
