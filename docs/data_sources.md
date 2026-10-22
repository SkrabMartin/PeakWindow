# Data sources and provenance

**No real source data is bundled in Phase 0.** The only data in the repository is the
synthetic fixture described in [`../data/fixtures/README.md`](../data/fixtures/README.md).

## Intended source

| Item | Value |
|---|---|
| Package | Open Power System Data (OPSD) Time series, release `2020-10-06` |
| Release page | https://data.open-power-system-data.org/time_series/2020-10-06/ |
| DOI / attribution | https://doi.org/10.25832/time_series/2020-10-06 |
| File | `time_series_60min_singleindex.csv` |
| Timestamp field | `utc_timestamp` |
| Load field | `CZ_load_actual_entsoe_transparency` |
| Upstream origin | ENTSO-E Transparency Platform (as stated by OPSD) |

## Verified on 2026-10-22 (metadata only)

The following facts come from the release's `datapackage.json`
(`https://data.open-power-system-data.org/time_series/2020-10-06/datapackage.json`).
**The CSV itself was not downloaded.**

- Package `name` is `opsd_time_series` and `version` is `2020-10-06`. Its `id` is the DOI above.
- Package `temporal` runs from `2014-12-27 23:00:00` to `2020-09-30 23:00:00`.
  This is package-wide and is **not** the Czech series' coverage.
- Resource `time_series_60min_singleindex.csv` exists. It is UTF-8 and comma-delimited,
  with a header row. Its declared size is 130,339,665 bytes and its schema lists 300 fields.
- The declared `hash` is `6a7f2bc571314cbf9c321cc03437691cd4be95c3a6f075e60ff99e8035c704c8`.
  It has 64 hex characters, but the metadata does not name the algorithm. SHA-256 is
  likely and **unverified**.
- Field `utc_timestamp` has type `datetime` and format `fmt:%Y-%m-%dT%H%M%SZ`. It is
  described as "Start of timeperiod in Coordinated Universal Time".
- Field `CZ_load_actual_entsoe_transparency` has type `number` and unit `MW`. It is
  described as "Total load in Czech Republic in MW as published on ENTSO-E Transparency
  Platform".
- Field `cet_cest_timestamp` (local time) also exists. PeakWindow will not use it as an index.

## Still unverified or assumed

- **Actual coverage of the Czech series:** first and last non-missing hours, plus the
  number and location of gaps. Nothing may be claimed until Phase 1 inspects the file.
- **Literal timestamp text in the CSV:** the metadata format string (`%H%M%S`, no colons)
  may not match what the file contains. The ingestion parser must handle the real format.
- **Hash algorithm**, and whether a download matches the declared hash.
- **License and terms:** `datapackage.json` contains no license field. The terms that
  apply (from OPSD and ENTSO-E) must be reviewed from the release page and upstream
  documentation before any redistribution. Until then, source data is neither committed
  nor redistributed.
- **Publication timing:** when each hourly value became publicly available. See the
  availability assumption in [methodology.md](methodology.md).
- `CZ_load_forecast_entsoe_transparency` may serve as a reference forecast, but only once
  its issuance timing is understood. It is not used in Phase 0.

## Planned provenance practice (Phase 1)

1. Download through an explicit, opt-in command into a git-ignored cache (`data/raw/`).
   Tests never download.
2. Record a manifest with the source URL, release, DOI, acquisition time (UTC) and file
   size. Also record the SHA-256 of the downloaded file and compare it with the declared hash.
3. Map columns explicitly: `utc_timestamp → timestamp_utc` and
   `CZ_load_actual_entsoe_transparency → load_mw`. Unused columns are dropped.
4. Run the Phase 0 validator on the mapped series and publish an actual coverage and gap audit.
5. Store the processed series (for example as Parquet) as a git-ignored derived file.

## Attribution boundary

PeakWindow's MIT license covers its own code and documentation only. OPSD and ENTSO-E
data keep their own terms and attribution requirements. Any published result must
cite the OPSD DOI above.
