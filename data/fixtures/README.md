# Synthetic fixtures

## `synthetic_hourly_load.csv`

> **SYNTHETIC.** This file contains no actual OPSD, ENTSO-E or other measured
> electricity data. Any result computed from it says nothing about real Czech
> electricity demand.

- Schema: canonical PeakWindow columns `timestamp_utc` (ISO 8601, `Z` suffix) and
  `load_mw` (integer megawatts).
- Coverage: 240 contiguous hourly rows, `2019-03-25T00:00:00Z` to
  `2019-04-03T23:00:00Z` (10 days). The span happens to include the Europe/Prague
  spring daylight-saving change (2019-03-31); in UTC it is a regular hourly grid.
- Values range from 5,169 to 8,575 MW: plausible magnitudes only, not a model of
  the Czech system.

### Generation rule

The file is fully deterministic (no random numbers). For `i = 0 .. 239`, with
`t = 2019-03-25T00:00:00Z + i hours`:

```python
load_mw = (
    7200
    + round(1300 * math.sin(2 * math.pi * (t.hour - 6) / 24))  # daily cycle (UTC hour)
    - (700 if t.weekday() >= 5 else 0)  # weekend reduction
    + ((i * 37) % 11 - 5) * 15  # fixed small ripple
)
```

Rows are written as `"{t:%Y-%m-%dT%H:%M:%SZ},{load_mw}"` under the header
`timestamp_utc,load_mw`. `tests/test_validation.py::test_fixture_matches_documented_generation_rule`
regenerates the rows from this rule and compares them with the committed file.

Failure cases (gaps, duplicates, naive timestamps, invalid load) are built as short
in-test frames or temporary files instead of committed fixtures.
