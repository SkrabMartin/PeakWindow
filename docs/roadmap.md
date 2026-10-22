# Roadmap

The project's minimum acceptance criteria (see [project_brief.md](project_brief.md))
also require a dashboard and a reproducible report, so they are not met yet.

| Phase | Scope | Status |
|---|---|---|
| 0. Foundations | Package, contracts, fixture validator, CLI, tests, CI, docs | **Done** |
| 1. Ingestion and quality audit | Opt-in OPSD download, checksum and manifest, column mapping, cached Parquet, actual Czech coverage and gap audit, license review, timestamp-alignment check | **Done** |
| 2. Leakage-safe features and baselines | Frozen split `split-v1` with a locked holdout; spike policy (scored as published); calendar and lag features from pre-origin data; future-mutation leakage tests; daily and weekly seasonal naive baselines; per-origin data-gap exceptions; experiment records | **Done** |
| 3. Learned models and chronological evaluation | Czech public-holiday feature; per-horizon Ridge selected on validation; pre-registered protocol; a single, final holdout evaluation | In progress |
| 4. Calibrated uncertainty and backtests | Conformal intervals from a separate calibration window; empirical coverage and width on the holdout; rolling-origin backtests | Planned |
| 5. Historical dashboard and optional packaging | Streamlit view of precomputed results, with prominent study-period and "historical reconstruction" labels; error-slice pages; optional hypothetical-threshold simulation; optional Docker and read-only deployment | Planned |
