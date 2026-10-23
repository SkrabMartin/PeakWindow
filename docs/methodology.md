# Methodology

> Phases 0–3 implement the time conventions, data-quality rules, the audited real
> series, leakage-safe features, the frozen split, two seasonal naive baselines and a
> Ridge model selected on validation. All numbers below are measured. Each one says
> which period it comes from.

## Time conventions (implemented in Phase 0)

- **Canonical index:** `timestamp_utc`, a timezone-aware UTC instant on an exact hour.
  UTC avoids the missing and repeated local hours of Europe/Prague daylight-saving
  changes. Local time is for display only, and any display must mark 23- and 25-hour
  local days.
- **Interval meaning:** an hourly timestamp labels the **start** of its hour
  (`10:00Z` covers 10:00 to 11:00 UTC). OPSD's metadata describes `utc_timestamp`
  as "Start of timeperiod in Coordinated Universal Time". In Phase 1, every one of the
  50,401 rows matched its `cet_cest_timestamp` label, with the correct Europe/Prague
  offset across all daylight-saving changes (see [data_sources.md](data_sources.md)).
- **Offsets:** offset-aware inputs are normalized to UTC (`01:00+01:00` → `00:00Z`).
  Naive timestamps are **rejected**, never assigned a guessed timezone.
- **Units:** load is in megawatts (MW). OPSD labels the Czech field "Total load ... in
  MW". Whether each value is an hourly mean must be confirmed from the source documentation.
- **Forecast origin:** exactly 00:00 UTC on a historical date. Other origins are rejected,
  and so is any horizon other than 24.
- **Targets:** exactly 24 timestamps, `origin + h` hours for `h = 0 … 23`.
- **Eligible inputs:** only observations with `timestamp_utc < origin`. The
  observation *at* the origin is the first target and is excluded.

### Availability assumption (unresolved)

An event time before the origin does not by itself prove that the value had been
**published** before the forecast was issued. The initial historical
reconstruction assumes **immediate availability** of all prior observations. Real
ENTSO-E publication delays are an explicit open limitation until they are audited. If delays
matter, the eligibility rule becomes `timestamp_utc < origin − publication_lag`.

### What the tests do and do not prove

The Phase 0 tests show that `select_history()` excludes the origin and every later row.
They also show that mutating, adding or removing rows at or after the origin, including
the exact-origin and last-target hours, leaves the selected history unchanged.
They do **not** prove that future feature generation is leakage-free. A later phase
must test the real feature builder by mutating future data and checking that the
features for an earlier origin are unchanged.

## Data quality rules (implemented in Phase 0)

The exact counting rules are in the docstring of `src/peakwindow/validation.py`. In summary:

- Invalid, naive and off-grid timestamps are counted separately.
- Duplicates are counted as extra occurrences beyond the first.
- Ordering is checked on valid timestamps in row order.
- Gaps are computed from unique valid timestamps.
- Load values are classified as missing, nonnumeric, nonfinite or negative. Zero is allowed.
- Nothing is repaired.

## Evaluation design (future phases)

### Chronological roles

All models share one set of documented forecast origins and split boundaries. Timestamps
are never shuffled randomly. Each period has one role:

| Period | Role | Allowed uses |
|---|---|---|
| Training | Fit model parameters | Fitting |
| Validation | Choose hyperparameters and features | Model selection |
| Calibration | Fit interval widths on out-of-sample residuals | Conformal calibration only |
| Final holdout | Report final metrics once | Evaluation only, **never tuning** |

### Frozen split (`split-v1`, frozen 2026-10-22)

The audited Czech series has load from `2015-01-01T01:00Z` to `2020-09-30T22:00Z`.
This bounds the usable forecast origins:

- **First origin: 2015-01-09.** The weekly naive baseline needs `y(origin − 168 h)`,
  and the first value is at 2015-01-01 01:00Z.
- **Last origin: 2020-09-29.** The origin 2020-09-30 would need a target at 23:00Z,
  which has no value.

| Period | Origins (00:00 UTC) | Role |
|---|---|---|
| Training | 2015-01-09 … 2018-12-31 | Fitting |
| Validation | 2019-01-01 … 2019-06-30 | Model and feature selection |
| Calibration | 2019-07-01 … 2019-12-31 | Interval calibration only |
| Final holdout | 2020-01-01 … 2020-09-29 (273 origins) | Final evaluation only, once |

The split was frozen at the start of Phase 2, before any baseline was scored, and is
defined in code (`src/peakwindow/splits.py`). It must not be changed after seeing
results. The holdout is **locked in code**: `evaluate_baselines` refuses it unless
explicitly unlocked, and no CLI command unlocks it before the final evaluation phase. The calibration window
follows validation and never overlaps the data used to fit the model it calibrates.
Rolling-origin backtests may follow. They refit using only the data available at
each origin.

**Known holdout caveat (measured):** monthly mean load in April and May 2020 was
11.3% and 12.5% below the same months of 2019, consistent with COVID-19 restrictions.
The holdout is therefore atypical. Results will be reported for the whole holdout
and, as a pre-declared sensitivity view, separately for 2020-04-01 … 2020-06-30
versus the remaining holdout months. Neither view replaces the other.

**Origins touching missing data** (the 104-hour run in November 2016 and the 3-hour
run in November 2017) are excluded under the data-gap policy below. This applies to
origins whose targets *or* required inputs fall in a missing hour. The exact list
is computed by code, not by hand.

### Models, in order

1. **Seasonal naive, daily:** `ŷ(origin + h) = y(origin + h − 24 h)`. For `h = 0 … 23`
   every input lies strictly before the origin.
2. **Seasonal naive, weekly:** `ŷ(origin + h) = y(origin + h − 168 h)`.
3. **Ridge regression** on calendar features and on lags available before the origin.
4. Optional: HistGradientBoostingRegressor, kept only if it genuinely beats Ridge on
   validation data.

### Metrics

- **MAE** and **RMSE** in MW over all evaluated target hours, plus per-horizon breakdowns.
- **Normalized MAE** = MAE / mean actual load over the same evaluated targets,
  reported as a percentage.
- **Relative MAE** = MAE_model / MAE_weekly-naive on the same targets (< 1 means better
  than the baseline). This measures skill against the baseline.
- Headline numbers always come with error distributions and the baseline comparison.
  No improvement is claimed unless it is measured on the holdout.

### Prediction intervals

Intervals with a stated target coverage (for example 80% or 90%) are calibrated
separately, for instance with split-conformal quantiles of absolute residuals from
the calibration window. The final holdout reports **empirical coverage** and
**mean interval width**.

### Data-gap policy

Every origin must produce 24 aligned targets. Otherwise the origin is excluded with
a documented data-gap exception. Gaps are never filled silently.

The Phase 1 audit also found a few isolated spikes, for example 2016-06-24 03:00Z
(+2,661 MW, then back down). **Decision (Phase 2, before scoring): they are scored
as published.** Any removal rule would be chosen after looking at the data, which is
exactly the kind of freedom that can bend results. The spikes stay listed in the audit
record, and they also enter lag features unchanged.

## Features (implemented in Phase 2)

`build_features()` returns 24 rows per origin. It restricts the series with
`select_history()` first, so load-derived values can only come from hours strictly
before the origin.

| Feature | Definition |
|---|---|
| `local_hour`, `local_weekday`, `month`, `is_weekend` | Calendar of the **target** in Europe/Prague time (known at issuance) |
| `is_holiday` | The target's local date is a Czech public holiday (added in Phase 3) |
| `lag_24h_is_holiday`, `lag_168h_is_holiday` | The local date of the matching lag hour is a public holiday (added in Phase 3) |
| `lag_24h_mw` | Load at `target − 24 h` |
| `lag_168h_mw` | Load at `target − 168 h` |
| `last_load_mw` | Load at `origin − 1 h` |
| `prev_24h_mean_mw` | Mean over `origin − 24 h … origin − 1 h`; missing if any hour is missing |

**Leakage test.** For three origins (an ordinary day and both 2019 DST days), the
features must be identical after each of these changes: the exact-origin value, the
last-target value, a value beyond the horizon, all future values scrambled, all
future rows removed, or all future values set to NaN. The same test is shown to fail
for a deliberately leaky builder, and it failed when a real leak was briefly injected
into the builder (lag 23 h without the history restriction).

**Exclusions.** An origin is scored only if all 24 targets **and** all features are
present, so every model is compared on exactly the same target hours.

## Baseline results (Phase 2, measured)

Measured with `peakwindow evaluate-baselines` on the audited OPSD series. These are
training and validation results, **not** the final holdout.

| Period | Origins scored | Model | MAE (MW) | RMSE (MW) | nMAE | Relative MAE |
|---|---|---|---|---|---|---|
| Training (2015-01-09 … 2018-12-31) | 1,439 of 1,453 | Daily naive | 451.1 | 691.2 | 6.05 % | 1.378 |
| | | **Weekly naive** | **327.4** | **546.8** | **4.39 %** | 1.000 |
| Validation (2019-01-01 … 2019-06-30) | 181 of 181 | Daily naive | 510.1 | 754.9 | 6.54 % | 1.342 |
| | | **Weekly naive** | **380.2** | **622.9** | **4.88 %** | 1.000 |

The 14 training exceptions are the origins touching the two missing runs in
November 2016 and November 2017. They were found by code and are listed in the experiment record.

Observations on validation:

- **The weekly naive is the reference to beat.** Its MAE is about 25% lower than the
  daily naive's.
- The daily naive fails on day-type changes. Its weekend MAE is 687 MW, against
  439 MW on weekdays, because Saturday copies Friday and Monday copies Sunday.
- Both baselines are worst around **public holidays**. The daily naive's worst days
  include 2019-01-02 and 2019-04-23, and the weekly naive's include 2019-01-07/08,
  2019-04-22 and 2019-04-29, each comparing with or following a holiday week.
  Christmas dominates the training-period worst days. A Czech public-holiday
  feature is therefore the first candidate for Phase 3.
- Error grows within the horizon toward midday. For the weekly naive, MAE is 314 MW at
  h = 0 and 433 MW at h = 12. The error is not monotonic in `h`, because the
  horizon is tied to time of day.

## Learned model and selection (Phase 3, measured on validation)

**Holiday calendar.** `holidays.py` computes the Czech public holidays: 11 fixed
dates, Easter Monday, and Good Friday from 2016. Easter uses the Gregorian computus.
The holiday features were added because Phase 2 validation showed that holidays
cause the largest baseline errors. That is a legitimate use of validation, and the
holdout played no part in it.

**Model.** `RidgeForecaster` fits one `StandardScaler → Ridge(alpha)` model per
horizon `h`. Two feature specifications were compared:

- `additive`: lags, last load, previous-day mean, holiday flags, local hour (a
  summer-time marker within one horizon), and weekday and month one-hot columns.
- `weekday_interactions`: `additive` plus `lag_24h` and `lag_168h` multiplied by each
  weekday indicator, so the weight on yesterday or last week can depend on the day type.

**Selection.** `peakwindow select-model` fits 12 candidates (2 specs × alpha in
{0.01, 0.1, 1, 10, 100, 1000}) on the 1,439 training origins and picks the lowest
validation MAE. **Chosen: `weekday_interactions`, alpha = 0.01.** The validation
curve is flat for alpha ≤ 1 (134.2–134.4 MW), so choosing the edge of the grid does
not matter in practice. A test confirms that selection is identical when every value
after the validation period is destroyed.

| Validation (181 origins) | MAE | RMSE | nMAE | Relative MAE | Abs. error p50 / p90 / p99 |
|---|---|---|---|---|---|
| Daily naive | 510.1 | 754.9 | 6.54 % | 1.342 | 288 / 1,410 / 2,263 |
| Weekly naive | 380.2 | 622.9 | 4.88 % | 1.000 | 230 / 808 / 2,679 |
| **Ridge** | **134.2** | **191.8** | **1.72 %** | **0.353** | **96 / 292 / 604** |

Checks run because the improvement is large:

- Validation MAE rises with the horizon, from 47 MW at h = 0 to about 185 MW around
  midday and about 125 MW late in the day.
- Destroying every value from the origin onward changed predictions by 0.0 MW.
- The in-sample training MAE is 108.6 MW, against 134.2 MW on validation.
- Without the three holiday features, validation MAE is 151.4 MW. With them it is
  134.2 MW, so they lower it by about 11 %.

The gain over the weekly naive has a plain explanation. On Tuesday–Friday, yesterday
is a far better guide than last week: the daily naive's MAE is 245 MW there. Ridge
learns per weekday which lag to trust. It also corrects the level with the last
observed hour.

## Final evaluation protocol (pre-registered before opening the holdout)

This protocol was committed to the repository **before** `evaluate-holdout` was
first run. It will not be changed after the results are seen.

1. The configuration recorded in `experiments/model_selection.json`
   (`weekday_interactions`, alpha = 0.01) is refitted on training + validation origins
   (2015-01-09 … 2019-06-30). The calibration period stays unused, so it remains
   out-of-sample for Phase 4.
2. Ridge and both seasonal naive baselines are scored once on all 273 holdout origins
   (2020-01-01 … 2020-09-29), on identical target hours, with the metrics defined above.
3. The headline result is the whole holdout. A pre-declared sensitivity view reports
   origins 2020-04-01 … 2020-06-30 (the COVID-19 demand drop) separately from the
   remaining holdout origins.
4. No re-tuning, feature changes or re-selection follow from the holdout. If Ridge
   does not beat the weekly naive there, that result is reported as it is.

## Final holdout results (Phase 3, measured once, 2026-10-22)

The holdout was run once with `peakwindow evaluate-holdout --confirm`, following the
protocol above. Ridge was refitted on 1,620 origins (train + validation). All 273
holdout origins (6,552 target hours) were scored, with no data-gap exceptions.

| Holdout 2020-01-01 … 2020-09-29 | MAE | RMSE | nMAE | Relative MAE | Abs. error p50 / p90 / p99 |
|---|---|---|---|---|---|
| Daily naive | 446.6 | 678.7 | 6.26 % | 1.549 | 238 / 1,297 / 2,103 |
| Weekly naive | 288.3 | 486.6 | 4.04 % | 1.000 | 160 / 681 / 1,996 |
| **Ridge** | **133.7** | **179.7** | **1.87 %** | **0.464** | **102 / 290 / 537** |

**Pre-declared sensitivity view:**

| | Weekly naive MAE | Ridge MAE | Ridge nMAE | Ridge relative MAE |
|---|---|---|---|---|
| 2020-04-01 … 2020-06-30 (COVID-19 drop, 91 origins) | 284.7 | 140.3 | 2.17 % | 0.493 |
| Other holdout origins (182) | 290.1 | 130.3 | 1.75 % | 0.449 |

Reading the result honestly:

- **Ridge generalized.** Its holdout MAE (133.7 MW) matches its validation MAE
  (134.2 MW). It beats the weekly naive in both sensitivity views, so the result
  does not depend on the COVID-19 period.
- **The margin over the weekly naive is smaller than on validation:** relative MAE
  is 0.464 on the holdout and 0.353 on validation. The weekly naive happened to do
  better in 2020 (288 MW, against 380 MW on validation). Absolute Ridge errors were
  stable.
- **The COVID-19 window is harder for Ridge** (nMAE 2.17 % against 1.75 %) but not
  broken. Lag features adapt to the lower level within days.
- **Holidays remain the weak spot.** Ridge MAE is 246 MW on holiday hours and 139 MW on
  weekdays. Its worst origin is 2020-07-05, the first of two consecutive holidays,
  which fell on a Sunday. The second worst is 2020-01-02, the day after New Year.
- Error by horizon runs from 36 MW at h = 0 to about 180 MW around midday, then down
  to about 105 MW late in the day.

What this result is **not**: it is not a live forecast. It assumes immediate
availability of past measurements, and it has no prediction interval yet (Phase 4).
It also covers a single country and a single nine-month test period.

## Rolling-origin backtest (Phase 4, measured, holdout excluded)

`peakwindow backtest` refits the selected configuration (`weekday_interactions`,
alpha = 0.01) once per test year. Each refit uses an expanding window of every
usable origin from 2015-01-09 up to 31 December of the previous year. Each fold is
then scored on its own year, together with both baselines. The 2020 holdout is never
read; a test confirms the backtest is identical when all 2020 data is destroyed.

| Test year | Fit origins | Test origins | Daily naive MAE | Weekly naive MAE | Ridge MAE | Ridge nMAE | Ridge relative MAE |
|---|---|---|---|---|---|---|---|
| 2016 | 357 | 355 | 445.9 | 334.2 | **126.5** | 1.71 % | 0.379 |
| 2017 | 712 | 362 | 459.4 | 345.4 | **120.0** | 1.59 % | 0.347 |
| 2018 | 1,074 | 365 | 461.7 | 354.6 | **127.5** | 1.67 % | 0.360 |
| 2019 | 1,439 | 365 | 480.9 | 359.3 | **126.4** | 1.67 % | 0.352 |

Ridge MAE stays within 120–128 MW in every year, even when fitted on a single year
(the 2016 fold). The 2020 holdout result (133.7 MW) is consistent with this range.
On holiday hours Ridge MAE is 234–303 MW per year, roughly twice its overall MAE.

Caveat: the configuration was chosen on 2019 H1 validation data, so for the
2016–2018 folds the *choice* of spec and alpha is informed by later data. The
coefficients themselves are always fitted on the past only. Two specs and six alphas
leave little room for that to matter.

## Prediction intervals (Phase 4)

**Method.** Split-conformal intervals (`uncertainty.py`), calibrated separately for
each horizon. The model is the final Ridge, refitted on train + validation, which is
identical to the model scored in the final holdout evaluation. For horizon `h` and
nominal level `1 − a`, the half-width `q_h` is the `ceil((n + 1)(1 − a))`-th smallest
absolute residual on the `n = 184` calibration origins (2019-07-01 … 2019-12-31).
The interval is `prediction ± q_h`. These calibration origins were never used to
fit or select the model.

**Calibration-period facts (no holdout data involved).** On the calibration period
Ridge MAE is 118.8 MW, against 338.7 MW for the weekly naive. Interval half-widths
grow with the horizon:

| Nominal level | Half-width h = 0 | Half-width h = 12 | Half-width h = 23 | Mean full width |
|---|---|---|---|---|
| 80 % | 77 MW | 231 MW | 164 MW | 351 MW |
| 90 % | 91 MW | 401 MW | 261 MW | 556 MW |

### Interval evaluation protocol (pre-registered before opening the holdout)

This protocol was committed **before** `evaluate-intervals` was first run. It will
not be changed after the results are seen.

1. The nominal levels are 80 % and 90 %, with per-horizon symmetric split-conformal
   half-widths exactly as defined above. Nothing is tuned afterwards: no widening
   factor, no other levels, no re-calibration.
2. Coverage is measured once on all 273 holdout origins (6,552 target hours). The
   report gives empirical coverage, mean width, coverage by horizon, the
   weekday/weekend/holiday split, and the shares below and above the interval.
3. As for the point forecasts, the 2020-04-01 … 2020-06-30 window is reported
   separately from the other holdout origins.
4. **Expectation stated in advance:** 2020 is not exchangeable with 2019 H2, mainly
   because of COVID-19 and a different season mix. Coverage may therefore fall short
   of nominal, especially on holidays. Any shortfall is reported as it is.

### Interval results on the holdout (measured once, 2026-10-23)

The run followed the protocol above. The refitted model was verified to be identical
to the one in the final point evaluation (`model_matches_final_holdout_model: true`).

| Nominal | Empirical coverage | Mean width | Weekday | Weekend | Holiday | Below lower | Above upper |
|---|---|---|---|---|---|---|---|
| 80 % | **72.6 %** | 351 MW | 70.3 % | 78.3 % | 41.9 % | 7.25 % | 20.16 % |
| 90 % | **90.2 %** | 556 MW | 90.0 % | 90.9 % | 62.3 % | 2.50 % | 7.26 % |

| Sensitivity view | 80 % coverage | 90 % coverage |
|---|---|---|
| 2020-04-01 … 2020-06-30 (91 origins) | 69.7 % | 87.0 % |
| Other holdout origins (182) | 74.0 % | 91.9 % |

Reading the result honestly:

- **The 90 % interval met its target (90.2 %). The 80 % interval did not: it covered
  72.6 %, 7.4 points short.** No widening factor or re-calibration was applied
  afterwards, as the protocol requires.
- **The misses are one-sided.** At the 80 % level the actual load ends above the
  interval 20.16 % of the time and below it only 7.25 % of the time. Ridge
  under-predicts in 2020 by 54.7 MW on average, against 17.8 MW on the calibration
  period, and by about 60–100 MW from May to September 2020. Symmetric intervals
  calibrated on 2019 H2 cannot absorb that shift. This is the non-exchangeability
  risk stated in advance. (The bias figures were computed after the run, to
  describe the result. Nothing was tuned on them.)
- **Holidays are badly covered** (41.9 % and 62.3 %), consistent with the larger
  holiday point errors. The calibration window has only a few holidays per horizon.
- The COVID-19 window covers less, but the shortfall is not limited to it. Outside
  the window, 80 % coverage is still 74.0 %.
- By horizon, 80 % coverage ranges from 60 % to 90 %. It is lowest in the morning and
  midday hours and highest at h = 0–1.

**Provenance note on recorded versions.** The `peakwindow_version` field in each
record comes from the installed package metadata, which was refreshed only on the
next `uv sync`. It therefore lags the source code by one version for some records.
The code that produced each record is identified by the commit that followed the run,
with no source changes in between:

| Record | Recorded version | Code committed in |
|---|---|---|
| `baselines_train.json`, `baselines_validation.json` | 0.3.0 | `2fafb2b` |
| `model_selection.json` | 0.3.0 | `ec20425` (0.4.0) |
| `final_holdout.json` | 0.4.0 | `ec20425` (0.4.0) |
| `backtest.json` | 0.4.0 | `444a817` (0.5.0) |
| `intervals_holdout.json` | 0.5.0 | `444a817` (0.5.0) |

The records were not regenerated to fix the field, because re-running the holdout
evaluations would be a second look at the holdout.

## Observation from the dashboard (Phase 5)

The daily-error timeline exposed the single worst Ridge day across all out-of-sample
forecasts. On **2016-04-28** (backtest 2016) Ridge's daily MAE is about 1,900 MW,
while the weekly naive's is 460 MW. Its input `last_load_mw` is the value at
2016-04-27 23:00Z: the isolated 8,706 MW spike from the Phase 1 audit, between values
near 6,900 MW. Ridge carries that jump into every hour of the day. The baselines do
not read that hour, so they were unaffected.

This is the cost of the "scored as published" spike policy, combined with a model
that trusts the latest hour. The model and the evaluation are frozen and are not
changed after the fact. A future version should test a robust latest-value input,
for example the median of the last three hours, or an input sanity check, on a new
pre-registered evaluation period.

Directions for future work, not tested here: conditional calibration (for example
separate holiday and ordinary-day widths), asymmetric intervals from signed residual
quantiles, and rolling re-calibration on recent residuals. Any of them would need a
new pre-registered evaluation period, because the 2020 holdout has now been seen.
