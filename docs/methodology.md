# Methodology

> Everything under "Evaluation design" is a **design commitment** for later phases.
> No model has been trained and no predictive result exists. Phases 0 and 1 implement
> the time conventions, the data-quality rules, the history-selection boundary and the
> audited real data series.

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

### Proposed split (from the Phase 1 audit)

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

This is a **proposal**. It is frozen at the start of Phase 2, before any baseline is
scored, and is not changed after seeing holdout results. The calibration window
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
(+2,661 MW, then back down). They are kept unchanged in the processed series. Phase 2
must decide, and document before scoring, whether such hours are scored as they are,
excluded from scoring, or reported both ways.
