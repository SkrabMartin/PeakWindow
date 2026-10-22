# data/

Only small, documented fixtures are committed here. **No real source data is
bundled in Phase 0.**

| Path | Status | Committed? |
|---|---|---|
| `fixtures/` | Small synthetic test inputs, documented in `fixtures/README.md` | Yes |
| `raw/`, `processed/` | Reserved for future downloads and derived files (Phase 1+) | No (git-ignored) |

Future real data (the OPSD time-series release) is downloaded as an explicit setup
step and cached locally; see [`../docs/data_sources.md`](../docs/data_sources.md).
Third-party data keeps its own terms and is **not** covered by this repository's
MIT code license.
