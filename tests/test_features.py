"""Feature builder: values, calendar semantics and future-mutation leakage tests."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest

from peakwindow.contracts import HOUR, LOAD_COLUMN, TIMESTAMP_COLUMN, ForecastRequest
from peakwindow.features import FEATURE_COLUMNS, KEY_COLUMNS, build_features

from conftest import hourly_series

Builder = Callable[[pd.DataFrame, ForecastRequest], pd.DataFrame]
ORIGIN = ForecastRequest.from_value("2019-06-10T00:00:00Z")


def series_around(
    origin: ForecastRequest, days_before: int = 10, days_after: int = 3
) -> pd.DataFrame:
    start = origin.origin_utc - pd.Timedelta(days=days_before)
    end = origin.origin_utc + pd.Timedelta(days=days_after)
    # Distinct, non-periodic values so any lookup error shows up.
    return hourly_series(str(start), str(end), lambda i: 5000 + 7 * i + (i * i) % 13)


def future_mutations(origin: pd.Timestamp) -> dict[str, Callable[[pd.DataFrame], pd.DataFrame]]:
    """Changes at or after the origin; none of them may alter any feature."""

    def set_at(timestamp: pd.Timestamp, value: float) -> Callable[[pd.DataFrame], pd.DataFrame]:
        def apply(frame: pd.DataFrame) -> pd.DataFrame:
            changed = frame.copy()
            changed.loc[changed[TIMESTAMP_COLUMN] == timestamp, LOAD_COLUMN] = value
            return changed

        return apply

    def scramble(frame: pd.DataFrame) -> pd.DataFrame:
        changed = frame.copy()
        future = changed[TIMESTAMP_COLUMN] >= origin
        changed.loc[future, LOAD_COLUMN] = changed.loc[future, LOAD_COLUMN].to_numpy()[::-1] * 3
        return changed

    def drop_future(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.loc[frame[TIMESTAMP_COLUMN] < origin].copy()

    def nan_future(frame: pd.DataFrame) -> pd.DataFrame:
        changed = frame.copy()
        changed.loc[changed[TIMESTAMP_COLUMN] >= origin, LOAD_COLUMN] = float("nan")
        return changed

    return {
        "exact_origin": set_at(origin, 1e9),
        "last_target": set_at(origin + 23 * HOUR, 1e9),
        "beyond_horizon": set_at(origin + 40 * HOUR, 1e9),
        "scramble_future": scramble,
        "drop_future": drop_future,
        "nan_future": nan_future,
    }


def assert_ignores_future(builder: Builder, series: pd.DataFrame, request: ForecastRequest) -> None:
    """The leakage test: features must be identical under every future mutation."""
    baseline = builder(series, request)
    for name, mutate in future_mutations(request.origin_utc).items():
        mutated = builder(mutate(series), request)
        try:
            pd.testing.assert_frame_equal(mutated, baseline)
        except AssertionError as exc:
            raise AssertionError(f"features changed under future mutation {name!r}") from exc


# --- leakage ----------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    ["2019-06-10T00:00:00Z", "2019-03-31T00:00:00Z", "2019-10-27T00:00:00Z"],
)
def test_features_ignore_all_future_mutations(origin: str) -> None:
    request = ForecastRequest.from_value(origin)
    assert_ignores_future(build_features, series_around(request), request)


def test_leakage_test_catches_a_deliberately_leaky_builder() -> None:
    """A builder that peeks at the target's own value must fail the same test."""

    def leaky_builder(series: pd.DataFrame, request: ForecastRequest) -> pd.DataFrame:
        features = build_features(series, request)
        by_time = series.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
        # Bug on purpose: "lag 0" is the value being forecast.
        features["lag_24h_mw"] = by_time.reindex(features["target_utc"]).to_numpy()
        return features

    with pytest.raises(AssertionError, match="future mutation"):
        assert_ignores_future(leaky_builder, series_around(ORIGIN), ORIGIN)


def test_pre_origin_change_does_change_features() -> None:
    """Guards against a vacuous leakage test."""
    series = series_around(ORIGIN)
    changed = series.copy()
    changed.loc[changed[TIMESTAMP_COLUMN] == ORIGIN.origin_utc - HOUR, LOAD_COLUMN] = 1.0

    before = build_features(series, ORIGIN)
    after = build_features(changed, ORIGIN)

    assert not before.equals(after)
    assert after["last_load_mw"].eq(1.0).all()


# --- values -----------------------------------------------------------------


def test_feature_values_match_definitions() -> None:
    series = series_around(ORIGIN)
    by_time = series.set_index(TIMESTAMP_COLUMN)[LOAD_COLUMN]
    origin = ORIGIN.origin_utc

    features = build_features(series, ORIGIN)

    assert list(features.columns) == [*KEY_COLUMNS, *FEATURE_COLUMNS]
    assert len(features) == 24
    assert features["horizon_h"].tolist() == list(range(24))
    for _, row in features.iterrows():
        target = row["target_utc"]
        assert row["lag_24h_mw"] == by_time[target - 24 * HOUR]
        assert row["lag_168h_mw"] == by_time[target - 168 * HOUR]
        assert target - 24 * HOUR < origin  # every lag is strictly pre-origin
    assert features["last_load_mw"].eq(by_time[origin - HOUR]).all()
    expected_mean = by_time[origin - 24 * HOUR : origin - HOUR].mean()
    assert features["prev_24h_mean_mw"].iloc[0] == pytest.approx(expected_mean)


def test_missing_inputs_stay_missing() -> None:
    series = series_around(ORIGIN)
    gap = ORIGIN.origin_utc - 20 * HOUR
    series.loc[series[TIMESTAMP_COLUMN] == gap, LOAD_COLUMN] = float("nan")

    features = build_features(series, ORIGIN)

    # The gap is lag_24h of the target at h = 4 and lies inside origin-24h..origin-1h.
    lag_24h_missing = features["lag_24h_mw"].isna()
    assert features.loc[lag_24h_missing, "horizon_h"].tolist() == [4]
    assert features["prev_24h_mean_mw"].isna().all()  # no partial means
    assert features["lag_168h_mw"].notna().all()
    assert features["last_load_mw"].notna().all()


def test_short_history_gives_missing_weekly_lag() -> None:
    features = build_features(series_around(ORIGIN, days_before=3), ORIGIN)

    assert features["lag_168h_mw"].isna().all()
    assert features["lag_24h_mw"].notna().all()


def test_local_calendar_follows_prague_across_dst() -> None:
    spring_request = ForecastRequest.from_value("2019-03-31T00:00:00Z")
    autumn_request = ForecastRequest.from_value("2019-10-27T00:00:00Z")
    spring = build_features(series_around(spring_request), spring_request)
    autumn = build_features(series_around(autumn_request), autumn_request)

    # 00:00Z is 01:00 CET; 01:00Z is already 03:00 CEST (02:00 local does not exist).
    assert spring["local_hour"].tolist()[:3] == [1, 3, 4]
    # 00:00Z is 02:00 CEST; 01:00Z is 02:00 CET again (the repeated hour).
    assert autumn["local_hour"].tolist()[:3] == [2, 2, 3]
    # Sunday 2019-03-31 in Prague ends at 22:00Z; the last two targets are Monday.
    assert spring["is_weekend"].tolist() == [True] * 22 + [False] * 2
    assert spring["local_weekday"].iloc[0] == 6


def test_duplicate_history_is_rejected() -> None:
    series = series_around(ORIGIN)
    duplicated = pd.concat([series, series.iloc[[5]]], ignore_index=True)

    with pytest.raises(ValueError, match="duplicate timestamps"):
        build_features(duplicated, ORIGIN)
