"""Czech public-holiday calendar."""

from __future__ import annotations

from datetime import date

import pytest

from peakwindow.holidays import czech_public_holidays, easter_sunday, is_czech_public_holiday


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2015, date(2015, 4, 5)),
        (2016, date(2016, 3, 27)),
        (2017, date(2017, 4, 16)),
        (2018, date(2018, 4, 1)),
        (2019, date(2019, 4, 21)),
        (2020, date(2020, 4, 12)),
        (2024, date(2024, 3, 31)),
    ],
)
def test_easter_sunday(year: int, expected: date) -> None:
    assert easter_sunday(year) == expected


def test_good_friday_is_a_holiday_only_from_2016() -> None:
    assert len(czech_public_holidays(2015)) == 12
    assert date(2015, 4, 3) not in czech_public_holidays(2015)
    assert date(2015, 4, 6) in czech_public_holidays(2015)  # Easter Monday

    assert len(czech_public_holidays(2016)) == 13
    assert {date(2016, 3, 25), date(2016, 3, 28)} <= czech_public_holidays(2016)


@pytest.mark.parametrize(
    "day",
    [
        "2019-01-01",
        "2019-05-01",
        "2019-05-08",
        "2019-07-05",
        "2019-07-06",
        "2019-09-28",
        "2019-10-28",
        "2019-11-17",
        "2019-12-24",
        "2019-12-25",
        "2019-12-26",
    ],
)
def test_fixed_holidays(day: str) -> None:
    assert is_czech_public_holiday(date.fromisoformat(day))


def test_ordinary_days_are_not_holidays() -> None:
    assert not is_czech_public_holiday(date(2019, 12, 27))
    assert not is_czech_public_holiday(date(2019, 4, 21))  # Easter Sunday itself
