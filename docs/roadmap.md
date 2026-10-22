# Roadmap

Completing Phase 0 does **not** satisfy the project's minimum acceptance criteria
(see [project_brief.md](project_brief.md)). It only establishes a tested foundation.

| Phase | Scope | Status |
|---|---|---|
| 0. Foundations | Package, contracts, fixture validator, CLI, tests, CI, docs | **Done** |
| 1. Ingestion and quality audit | Opt-in OPSD download, checksum and manifest, column mapping, cached Parquet, actual Czech coverage and gap audit, license review, timestamp-alignment check | Next |
| 2. Leakage-safe features and baselines | Calendar and lag features built only from pre-origin data; future-mutation leakage tests on the feature builder; daily and weekly seasonal naive baselines; per-origin data-gap exceptions | Planned |
| 3. Learned models and chronological evaluation | Ridge (optionally gradient boosting) on fixed chronological splits chosen from the audit; MAE, RMSE, normalized and relative MAE; error distributions; local experiment records (config, seed, date ranges, hashes) | Planned |
| 4. Calibrated uncertainty and backtests | Conformal intervals from a separate calibration window; empirical coverage and width on the holdout; rolling-origin backtests | Planned |
| 5. Historical dashboard and optional packaging | Streamlit view of precomputed results, with prominent study-period and "historical reconstruction" labels; error-slice pages; optional hypothetical-threshold simulation; optional Docker and read-only deployment | Planned |

Each phase gets its own completion report, like [PHASE_0_REPORT.md](PHASE_0_REPORT.md).
