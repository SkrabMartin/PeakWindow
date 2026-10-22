"""Forecast-request contract and history-selection boundary tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

import pandas as pd
import pytest

from peakwindow.contracts import (
    LOAD_COLUMN,
    TIMESTAMP_COLUMN,
    ContractError,
    ForecastRequest,
    select_history,
    target_timestamps,
)

ORIGIN = "2019-06-10T00:00:00Z"

Mutation = Callable[[pd.DataFrame], pd.DataFrame]


def hourly_frame(start: str, hours: int, base: float = 7000.0) -> pd.DataFrame:
    timestamps = pd.date_range(start=start, periods=hours, freq="h", tz="UTC")
    return pd.DataFrame(
        {TIMESTAMP_COLUMN: timestamps, LOAD_COLUMN: [base + i for i in range(hours)]}
    )


# --- forecast request -------------------------------------------------------


def test_targets_are_24_ordered_hours_starting_at_origin() -> None:
    targets = target_timestamps(ForecastRequest.from_value(ORIGIN))

    assert len(targets) == 24
    assert targets[0] == pd.Timestamp("2019-06-10T00:00:00Z")
    assert targets[-1] == pd.Timestamp("2019-06-10T23:00:00Z")
    assert targets.is_monotonic_increasing
    assert (targets[1:] - targets[:-1] == pd.Timedelta(hours=1)).all()
    assert str(targets.tz) == "UTC"


def test_offset_aware_origin_is_normalized_to_utc() -> None:
    prague_summer = timezone(timedelta(hours=2))
    request = ForecastRequest.from_value(datetime(2019, 6, 10, 2, 0, tzinfo=prague_summer))

    assert request.origin_utc == pd.Timestamp("2019-06-10T00:00:00Z")
    assert str(request.origin_utc.tz) == "UTC"


@pytest.mark.parametrize(
    ("origin", "message"),
    [
        ("2019-06-10T01:00:00Z", "exactly 00:00 UTC"),
        ("2019-06-10T00:30:00Z", "exactly 00:00 UTC"),
        ("2019-06-10T00:00:01Z", "exactly 00:00 UTC"),
        # 00:00 Prague time is 22:00 UTC the previous day, so not a valid origin.
        ("2019-06-10T00:00:00+02:00", "exactly 00:00 UTC"),
        ("2019-06-10T00:00:00", "timezone-naive"),
        ("not-a-date", "not a valid ISO 8601"),
    ],
)
def test_invalid_origins_are_rejected(origin: str, message: str) -> None:
    with pytest.raises(ContractError, match=message):
        ForecastRequest.from_value(origin)


def test_naive_datetime_origin_is_rejected() -> None:
    with pytest.raises(ContractError, match="timezone-naive"):
        ForecastRequest(origin_utc=pd.Timestamp("2019-06-10T00:00:00"))


@pytest.mark.parametrize("horizon", [0, 1, 23, 25, 48, -24, True, 24.0, "24"])
def test_unsupported_horizons_are_rejected(horizon: object) -> None:
    origin = pd.Timestamp(ORIGIN)
    with pytest.raises(ContractError, match="horizon_hours"):
        ForecastRequest(origin_utc=origin, horizon_hours=horizon)  # type: ignore[arg-type]


def test_request_is_immutable() -> None:
    request = ForecastRequest.from_value(ORIGIN)
    with pytest.raises(AttributeError):
        request.horizon_hours = 48  # type: ignore[misc]


# --- history selection boundary --------------------------------------------


def test_history_excludes_origin_and_everything_after() -> None:
    frame = hourly_frame("2019-06-08T00:00:00Z", hours=24 * 4)
    request = ForecastRequest.from_value(ORIGIN)

    history = select_history(frame, request)

    assert len(history) == 48
    assert history[TIMESTAMP_COLUMN].max() == pd.Timestamp("2019-06-09T23:00:00Z")
    assert (history[TIMESTAMP_COLUMN] < request.origin_utc).all()
    assert request.origin_utc not in set(history[TIMESTAMP_COLUMN])


def test_history_preserves_order_and_does_not_mutate_input() -> None:
    frame = hourly_frame("2019-06-09T00:00:00Z", hours=48).iloc[::-1]
    original = frame.copy()

    history = select_history(frame, ForecastRequest.from_value(ORIGIN))

    pd.testing.assert_frame_equal(frame, original)
    assert list(history.index) == list(range(23, -1, -1))


def _future_mutations() -> dict[str, Mutation]:
    """Ways of changing data at or after the origin that must not affect history."""
    origin = pd.Timestamp(ORIGIN)
    last_target = origin + pd.Timedelta(hours=23)

    def mutate_at(timestamp: pd.Timestamp, value: float) -> Mutation:
        def apply(frame: pd.DataFrame) -> pd.DataFrame:
            changed = frame.copy()
            changed.loc[changed[TIMESTAMP_COLUMN] == timestamp, LOAD_COLUMN] = value
            return changed

        return apply

    def scramble_all_future(frame: pd.DataFrame) -> pd.DataFrame:
        changed = frame.copy()
        future = changed[TIMESTAMP_COLUMN] >= origin
        changed.loc[future, LOAD_COLUMN] = -1.0e9
        return changed

    def drop_all_future(frame: pd.DataFrame) -> pd.DataFrame:
        return frame.loc[frame[TIMESTAMP_COLUMN] < origin].copy()

    def append_future_rows(frame: pd.DataFrame) -> pd.DataFrame:
        extra = hourly_frame("2019-06-12T00:00:00Z", hours=72, base=99999.0)
        extra.index = extra.index + 10_000
        return pd.concat([frame, extra])

    def duplicate_origin_row(frame: pd.DataFrame) -> pd.DataFrame:
        duplicate = frame.loc[frame[TIMESTAMP_COLUMN] == origin].copy()
        duplicate.index = duplicate.index + 20_000
        duplicate[LOAD_COLUMN] = 1.0
        return pd.concat([frame, duplicate])

    return {
        "exact_origin_value": mutate_at(origin, 123456.0),
        "late_horizon_value": mutate_at(last_target, 123456.0),
        "beyond_horizon_value": mutate_at(origin + pd.Timedelta(hours=30), 123456.0),
        "scramble_all_future": scramble_all_future,
        "drop_all_future": drop_all_future,
        "append_future_rows": append_future_rows,
        "duplicate_origin_row": duplicate_origin_row,
    }


@pytest.mark.parametrize("mutation", sorted(_future_mutations()))
def test_history_is_invariant_to_changes_at_or_after_origin(mutation: str) -> None:
    frame = hourly_frame("2019-06-03T00:00:00Z", hours=24 * 9)
    request = ForecastRequest.from_value(ORIGIN)
    baseline = select_history(frame, request)

    mutate = _future_mutations()[mutation]
    mutated_history = select_history(mutate(frame), request)

    pd.testing.assert_frame_equal(mutated_history, baseline)


def test_changing_a_pre_origin_value_does_change_history() -> None:
    """Guards against a vacuous invariance test (e.g. selection returning nothing)."""
    frame = hourly_frame("2019-06-08T00:00:00Z", hours=24 * 4)
    request = ForecastRequest.from_value(ORIGIN)
    changed = frame.copy()
    last_input = pd.Timestamp("2019-06-09T23:00:00Z")
    changed.loc[changed[TIMESTAMP_COLUMN] == last_input, LOAD_COLUMN] = 1.0

    assert not select_history(changed, request).equals(select_history(frame, request))


def test_history_requires_utc_aware_timestamp_column() -> None:
    request = ForecastRequest.from_value(ORIGIN)
    naive = pd.DataFrame(
        {TIMESTAMP_COLUMN: pd.date_range("2019-06-09", periods=3, freq="h"), LOAD_COLUMN: 1.0}
    )
    prague = naive.assign(
        **{TIMESTAMP_COLUMN: naive[TIMESTAMP_COLUMN].dt.tz_localize("Europe/Prague")}
    )

    with pytest.raises(ContractError, match="UTC datetime dtype"):
        select_history(naive, request)
    with pytest.raises(ContractError, match="UTC datetime dtype"):
        select_history(prague, request)
    with pytest.raises(ContractError, match="missing required column"):
        select_history(pd.DataFrame({LOAD_COLUMN: [1.0]}), request)


def test_history_accepts_datetime_utc_tzinfo() -> None:
    timestamps = [datetime(2019, 6, 9, 23, tzinfo=UTC), datetime(2019, 6, 10, 0, tzinfo=UTC)]
    frame = pd.DataFrame({TIMESTAMP_COLUMN: pd.to_datetime(timestamps), LOAD_COLUMN: [1.0, 2.0]})

    history = select_history(frame, ForecastRequest.from_value(ORIGIN))

    assert history[LOAD_COLUMN].tolist() == [1.0]
