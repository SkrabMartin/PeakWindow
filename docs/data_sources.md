# Data sources and provenance

**No real source data is committed to this repository.** The OPSD file is downloaded
by `peakwindow fetch-opsd` into git-ignored `data/raw/`, and derived files go to
git-ignored `data/processed/`. The only committed data is the synthetic fixture in
[`../data/fixtures/`](../data/fixtures/README.md).

## Source

| Item | Value |
|---|---|
| Package | Open Power System Data (OPSD) Time series, release `2020-10-06` |
| Release page | https://data.open-power-system-data.org/time_series/2020-10-06/ |
| DOI | https://doi.org/10.25832/time_series/2020-10-06 |
| File | `time_series_60min_singleindex.csv` (130,339,665 bytes) |
| SHA-256 | `6a7f2bc571314cbf9c321cc03437691cd4be95c3a6f075e60ff99e8035c704c8` |
| Columns used | `utc_timestamp` → `timestamp_utc`, `CZ_load_actual_entsoe_transparency` → `load_mw` |
| Column used for checks only | `cet_cest_timestamp` |
| Upstream origin | ENTSO-E Transparency Platform (as stated by OPSD) |

**Required citation** (from the release README):

> Open Power System Data. 2020. Data Package Time series. Version 2020-10-06.
> https://doi.org/10.25832/time_series/2020-10-06. (Primary data from various
> sources, for a complete list see URL).

## Verified facts (2026-10-22)

These come from the release metadata (`datapackage.json`, README), from the file
downloaded on 2026-10-22, and from running `peakwindow prepare-opsd` on it.

### File and metadata

- The downloaded file's SHA-256 equals the hash declared in `datapackage.json`, so
  the declared hash **is SHA-256**. Its size matches the declared size.
- `utc_timestamp` is described as "Start of timeperiod in Coordinated Universal
  Time". Its literal text is `2014-12-31T23:00:00Z`, ISO 8601 with colons, which
  differs from the metadata format string `%Y-%m-%dT%H%M%SZ`.
- `cet_cest_timestamp` is written like `2015-01-01T00:00:00+0100` (offset without a colon).
- `CZ_load_actual_entsoe_transparency` has unit `MW`, described as "Total load in
  Czech Republic in MW as published on ENTSO-E Transparency Platform". Missing values
  are empty cells.

### Timestamp grid and alignment

- 50,401 rows, from `2014-12-31T23:00:00Z` to `2020-09-30T23:00:00Z`. Every timestamp
  is valid UTC on an exact hour. None is duplicated, the rows are in order, and the
  hourly grid has no gaps.
- **UTC vs CET/CEST alignment passed on all 50,401 rows.** Each row's local label is
  the same instant as its UTC label and carries the correct Europe/Prague offset,
  including every daylight-saving change. This supports the "start of the hour"
  reading of both columns.

### Czech load coverage

| Measure | Value |
|---|---|
| First hour with load | `2015-01-01T01:00:00Z` |
| Last hour with load | `2020-09-30T22:00:00Z` |
| Hours in that span | 50,398 |
| Rows outside the span without load | 2 leading, 1 trailing (package-wide padding) |
| Hours without load inside the span | 107, in 2 runs |
| Missing run 1 | `2016-11-10T07:00:00Z` – `2016-11-14T14:00:00Z` (104 h) |
| Missing run 2 | `2017-11-09T08:00:00Z` – `2017-11-09T10:00:00Z` (3 h) |
| Nonnumeric, infinite, negative or zero values | none |
| Load min / mean / max | 4,246.49 / 7,432.23 / 11,142.18 MW |
| Longest run of identical consecutive values | 1 h (no stuck values) |

Coverage by UTC year (hours with load / hours in span): 2015: 8,759/8,759 ·
2016: 8,680/8,784 · 2017: 8,757/8,760 · 2018: 8,760/8,760 · 2019: 8,760/8,760 ·
2020 (to 30 Sep 22:00Z): 6,575/6,575.

### Suspicious values (kept, not changed)

The largest hour-to-hour changes include short spikes that look like reporting
artifacts rather than real demand:

- `2016-06-24T03:00Z`: 5,752 → **8,413** → 6,978 MW (+2,661 MW, then −1,435 MW)
- `2016-04-27T23:00Z`: 6,942 → **8,706** → 6,918 MW (+1,764 MW, then −1,788 MW)
- `2016-06-28T04:00Z`: +1,878 MW

No outlier threshold has been applied, and nothing was removed or smoothed. The
full top-10 list is in the audit record. How evaluation treats these hours is a
Phase 2 decision, to be made before any model is scored.

## Still unverified or open

- **Data license:** neither `datapackage.json` nor the README states a license for
  the data. The release page licenses its *text* under CC-BY. Primary data comes
  from ENTSO-E Transparency, whose terms apply. PeakWindow therefore **does not
  redistribute** the data: it is downloaded by each user and never committed. Any
  published result must carry the citation above.
- **Publication timing:** when each hourly value became public on ENTSO-E is unknown.
  Immediate availability is still assumed (see [methodology.md](methodology.md)).
- **Exact meaning of "Total load":** whether the values are hourly means and how
  ENTSO-E defines Czech total load has not been checked against ENTSO-E documentation.
- **Cause of the spikes and missing runs:** not investigated upstream.

## Provenance practice (implemented)

1. `peakwindow fetch-opsd` downloads to `data/raw/opsd/2020-10-06/<file>.part`,
   checks the size and SHA-256 against the declared values, and only then moves the
   file into place. A mismatch leaves nothing in the cache.
2. Next to the file, `<file>.manifest.json` records the URL, acquisition time (UTC),
   size, SHA-256, declared values, PeakWindow version and the citation.
3. A cached file is reused only if its manifest exists and its SHA-256 still
   matches. Otherwise the user must re-download with `--force`.
4. `peakwindow prepare-opsd` re-verifies the SHA-256, maps the two columns, checks
   alignment and runs the audit. It writes `data/processed/opsd_2020-10-06_cz_load.parquet`,
   trimmed to the observed span with missing hours kept as NaN, and
   `opsd_2020-10-06_cz_load.audit.json`, which links the raw SHA-256, the alignment
   result, the audit and the Parquet file's SHA-256.

## Attribution boundary

PeakWindow's MIT license covers its own code and documentation only. OPSD and
ENTSO-E data keep their own terms and attribution requirements.
