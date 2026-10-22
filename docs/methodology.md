# Methodology

> Phases 0–2 implement the time conventions, data-quality rules, the audited real
> series, leakage-safe features, the frozen split and two seasonal naive baselines.
> **No learned model exists yet.** The only measured results are baseline errors on
> the training and validation periods; the final holdout has not been evaluated.

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
