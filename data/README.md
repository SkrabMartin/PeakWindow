# data/

Only small, documented fixtures are committed here. **No real source data is
committed.** It is downloaded locally by each user.

| Path | Status | Committed? |
|---|---|---|
| `fixtures/` | Small synthetic test inputs, documented in `fixtures/README.md` | Yes |
| `raw/opsd/2020-10-06/` | OPSD file and its provenance manifest, written by `peakwindow fetch-opsd` | No (git-ignored) |
| `processed/` | Czech load Parquet series and audit record, written by `peakwindow prepare-opsd` | No (git-ignored) |

Downloading is an explicit setup step. Everything after it runs offline. See
[`../docs/data_sources.md`](../docs/data_sources.md).
Third-party data keeps its own terms and is **not** covered by this repository's
MIT code license.
