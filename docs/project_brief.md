# Project brief (design source)

> **Status note:** this is the original design brief for PeakWindow. It is
> preserved here verbatim, below this note, as the design source. It describes the
> **eventual product**. Only the foundations and data ingestion are implemented. What exists
> today is listed in the [README](../README.md). Statements about the
> OPSD dataset are the brief's planning assumptions; verified metadata is recorded
> separately in [`data_sources.md`](data_sources.md). The repository structure
> suggested at the end of the brief is a target layout, not the current one.

---

# 03 — PeakWindow: Electricity Demand Forecasting Laboratory

**Classification:** Data Science (primary), Machine Learning Engineering (secondary)  
**Core goal:** Produce honest, reproducible next-24-hour electricity-load forecasts for Czech historical data with explicit uncertainty and meaningful baselines.  
**Independent repository:** `peakwindow/`  
**Budget:** 0 CZK local and CPU-only; optional lightweight hosted dashboard.  
**Recommended audience:** Industrial analytics teams, applied data scientists, energy-tech companies, ML evaluation teams.

## Detailed description (500+ words)

PeakWindow is an end-to-end forecasting and decision-analysis tool for electricity demand. It uses the Czech electricity-load measurements included in the public Open Power System Data (OPSD) time-series package. The official package includes hourly load readings and related market and generation signals from historical European power-system sources; the published October 2020 release covers approximately 2015 through mid-2020. That history is sufficient for a rigorous offline forecasting study, but it is **not** a current live forecasting feed. The application must prominently label its study period and forecast issue time, because a prediction plotted on a modern dashboard can easily look operational even when it was created from historical data.

The main user is an analyst or power-system engineering student asking a meaningful question: given the demand measurements available up to this morning, how well can a model forecast the next 24 hourly load values? The product lets that user choose a historical forecast date and compare several forecast strategies with the actual load that was subsequently measured. A visual display shows measured megawatts, predicted megawatts, an uncertainty band, and the timing and size of daily demand peaks. A second page reports longer-term performance across many historical forecast origins rather than offering one cherry-picked successful prediction. A third page investigates failures: weekends, seasonal changes, high-load periods, missing data intervals and unusual dates.

PeakWindow begins with a transparent ingestion process. Downloading the OPSD dataset is an explicit setup step; after the file is cached locally, the analysis is reproducible offline. The pipeline selects the Czech load series and its UTC timestamp, parses types, checks hourly chronology, and generates a gap report. UTC is the canonical indexing system, so daylight saving transitions cannot silently create duplicate or missing time identifiers. For display, the app can convert timestamps to Europe/Prague and explicitly mark any local-day irregularities. The dataset is saved to Parquet and read through Pandas or DuckDB for fast subsets. Raw source files should not be committed in full unless redistribution rights and repository size justify it; a small documented fixture allows tests to run without downloading hundreds of megabytes.

The mathematical definition of each prediction matters more than the choice of model. The forecast origin is 00:00 UTC on a selected historical date; the task is to forecast load for the 24 hourly timestamps beginning at that origin. Features may include hour of day, day of week, month, a weekend flag, and lagged observed load from hours that were already available **before** the forecast origin. A feature generated at 10:00 tomorrow must not accidentally incorporate electricity demand measured this afternoon. This rule should be enforced by the feature builder and by an automated leakage test that changes all future observations and checks that the features for an earlier origin do not change. If the project later uses weather features, it must use historically available forecasts at issuance time, not future observed weather values.

The first models are intentionally simple. A same-hour-previous-day seasonal naive prediction and a same-hour-previous-week prediction establish a realistic baseline. An interpretable linear model such as Ridge regression comes next. A CPU-friendly nonlinear model such as HistGradientBoostingRegressor is optional and should be kept only if it adds genuine value. All models receive exactly the same train, validation and test boundaries and documented forecast origins. There is no random shuffling of timestamps. A credible initial split might train on 2015–2018, tune on 2019, and hold out the available part of 2020 for final evaluation, adjusted only after checking the exact coverage and missingness of the selected Czech series. For a stronger study, the team performs rolling-origin backtests that retrain or refit using only information that would have existed at each point in time.

Evaluation should present MAE and RMSE in megawatts, plus a normalized metric for comparison across intervals. A headline metric must be accompanied by error distributions and comparison with the seasonal naive baseline. Do not claim that a complex model is better just because it has more parameters. PeakWindow should also output a prediction interval with a target historical coverage, such as 80% or 90%. A simple conformal calibration strategy can be applied to residuals from a chronologically separate calibration window; the final holdout evaluates whether the target coverage is actually achieved. The report displays empirical coverage and typical interval width so a user can distinguish an informative interval from an absurdly wide one.

An optional decision layer translates forecast quality into an understandable exercise without claiming to operate the grid. For example, define a user-controlled hypothetical capacity threshold and flag forecasted hours likely to exceed it. Show the trade-off between false alarms and missed peaks based on historical outcomes. This is a **simulation**, not a dispatch instruction. Provide assumptions and a clear statement that national demand forecasts do not reveal local network constraints. The UI can let users compare a conservative model with a sharper but more error-prone one; it can also show how greater uncertainty during special periods affects the usefulness of decisions.

The engineering component consists of reproducible CLI commands for dataset preparation, feature generation, training, backtesting, and offline prediction. Each experiment stores its configuration, software version, training date range, metrics, artifact hash and output figures in a local experiment folder. No hosted experiment tracker is needed. A Streamlit application loads precomputed predictions and metrics rather than retraining a model on every page refresh. For optional deployment, the dashboard can be hosted as a read-only service that carries a compact model or saved forecast artifacts; CPU-intensive retraining remains a local, explicit operation. The finished result shows a recruiter that you understand the statistical and operational subtleties of time-series machine learning: reproducibility, benchmark selection, leakage control, uncertainty, evaluation design and honest limitations.

## Proposed technology stack

- **Python 3.12+, NumPy, Pandas** for time-series manipulation and statistical processing.
- **scikit-learn** for lag-feature models, cross-validation utilities, baselines, Ridge and HistGradientBoosting.
- **SciPy or a small explicit conformal-calibration routine** for uncertainty analysis; avoid unnecessarily heavy ML frameworks.
- **DuckDB + Parquet** for compact cached historical data and efficient queries.
- **Matplotlib / Plotly + Streamlit** for forecast exploration, uncertainty and error diagnostics.
- **pytest, Ruff, mypy, GitHub Actions** for correctness and reproducibility.
- **Docker** for the demo application; **optional FastAPI** for `/forecast`, `/metrics`, and `/health` if it adds real value.

## MVP vs maximum scope

| Dimension | Minimum complete version | Advanced / maximum version |
|---|---|---|
| Geography | Czech electricity-load history only | Several countries and an explicit benchmark across regions |
| Models | Daily and weekly seasonal naive + Ridge | Gradient boosting, systematic hyperparameter search, calibrated intervals |
| Evaluation | Fixed chronological split, MAE/RMSE, error plots | Rolling-origin backtesting, uncertainty coverage, performance drift study |
| Data integrity | UTC parsing, missing-gap report, cached local data | Publication-lag assumptions, reproducible dataset manifest, additional source audit |
| UI | View a historical 24-hour forecast and actual curve | Model comparison, date sliders, error-slice dashboard, hypothetical peak thresholds |
| Deploy | Local/offline Streamlit | Read-only web deployment and optional forecast API; retrain offline |

## Minimum acceptance criteria

1. The source dataset can be downloaded once, cached, and processed offline; the test suite uses a tiny included fixture.
2. Every forecast origin produces exactly 24 timestamp-aligned outputs or documents a data-gap exception.
3. At least two seasonal naive baselines and one learned model are compared fairly.
4. A leakage test demonstrates that altering observations after the forecast origin does not affect its input features.
5. Results report error values from a true held-out chronological interval, with no claims of improvement unless measured.
6. The dashboard shows study-period dates and clarifies that historical reconstructions are not real-time forecasts.
7. A reproducible report lists configuration, seed, split boundaries, MAE, RMSE, and plots.

## Suggested repository structure

```text
peakwindow/
  data/                  # metadata and small demo fixtures only
  src/peakwindow/
    ingest.py             # OPSD source parser
    features.py           # timestamp-safe lag/calendar features
    baselines.py          # naive predictions
    models.py             # train/evaluate code
    uncertainty.py        # calibration and interval evaluation
    backtest.py           # rolling-origin experiment loop
    reporting.py          # metrics and plots
  dashboard/              # Streamlit historical demo
  tests/                  # time leakage and regression tests
  experiments/            # generated results, not committed large blobs
  docs/                   # experiment methodology / limitations
  pyproject.toml
  Dockerfile
  README.md
```

## Portfolio role / recruiter demonstration

**Signals:** applied statistics, time-series modeling, reproducible ML, scientific reasoning, data leakage prevention, uncertainty estimation, visual explanation.  
**Two-minute demo:** choose a historical forecast date → overlay prediction with truth → compare against the last-week baseline → inspect worst-performing weekends → show a failing leakage test intentionally constructed and then fixed.  
**Important boundary:** not a live grid-control system, no guarantee of economic benefit, and no deployment decision based solely on historical backtests.

## Dataset and source references

- Open Power System Data time-series package (historical): https://data.open-power-system-data.org/time_series/2020-10-06/
- Dataset DOI / attribution: https://doi.org/10.25832/time_series/2020-10-06
- Select `CZ_load_actual_entsoe_transparency` from `time_series_60min_singleindex.csv` after verifying the exact file/schema. OPSD also lists `CZ_load_forecast_entsoe_transparency`; treat published forecasts as a separately evaluated reference only when their timing is understood.
