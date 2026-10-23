# Roadmap

All five phases are complete. Measured against the brief's minimum acceptance criteria
(see [project_brief.md](project_brief.md)):

| Criterion | Status |
|---|---|
| 1. Download once, cache, process offline; tests use a tiny fixture | Met |
| 2. 24 aligned outputs per origin, or a documented data-gap exception | Met |
| 3. Two seasonal naive baselines and one learned model compared fairly | Met |
| 4. A leakage test shows that changing data after the origin leaves features unchanged | Met |
| 5. Errors from a true chronological holdout; no unmeasured claims | Met |
| 6. The dashboard shows study-period dates and says it is not real time | Met |
| 7. A reproducible report with configuration, seed, split boundaries, MAE, RMSE and plots | Met: `peakwindow report` writes tables and four SVG figures |

| Phase | Scope | Status |
|---|---|---|
| 0. Foundations | Package, contracts, fixture validator, CLI, tests, CI, docs | **Done** |
| 1. Ingestion and quality audit | Opt-in OPSD download, checksum and manifest, column mapping, cached Parquet, actual Czech coverage and gap audit, license review, timestamp-alignment check | **Done** |
| 2. Leakage-safe features and baselines | Frozen split `split-v1` with a locked holdout; spike policy (scored as published); calendar and lag features from pre-origin data; future-mutation leakage tests; daily and weekly seasonal naive baselines; per-origin data-gap exceptions; experiment records | **Done** |
| 3. Learned models and chronological evaluation | Czech public-holiday feature; per-horizon Ridge selected on validation; pre-registered protocol; a single, final holdout evaluation (Ridge MAE 133.7 MW vs 288.3 MW for the weekly naive) | **Done** |
| 4. Calibrated uncertainty and backtests | Per-horizon split-conformal intervals from the 2019 H2 calibration window; pre-registered holdout coverage (90 % met, 80 % at 72.6 %); rolling-origin backtest 2016–2019 | **Done** |
| 5. Historical dashboard and report | Streamlit view of precomputed forecasts with "historical reconstruction" labels, error slices and worst days; Markdown report with SVG figures from the records. Not done: threshold simulation, Docker, deployment | **Done** |

Possible next steps, each needing a new pre-registered evaluation period: robust
latest-value inputs, holiday-aware or asymmetric intervals, and gradient boosting.
