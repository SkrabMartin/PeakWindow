"""Czech public holidays (státní svátky a ostatní svátky), computed, not downloaded.

Fixed-date holidays: 1 Jan, 1 May, 8 May, 5 Jul, 6 Jul, 28 Sep, 28 Oct, 17 Nov,
24 Dec, 25 Dec, 26 Dec. Movable holidays: Easter Monday, and Good Friday from 2016
on, when it became a public holiday. Easter Sunday follows the Gregorian computus.
Dates are local (Europe/Prague) calendar dates.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import cache

FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1),
    (5, 1),
    (5, 8),
    (7, 5),
    (7, 6),
    (9, 28),
    (10, 28),
    (11, 17),
    (12, 24),
    (12, 25),
    (12, 26),
)
GOOD_FRIDAY_FIRST_YEAR = 2016


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - name from the published algorithm
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


@cache
def czech_public_holidays(year: int) -> frozenset[date]:
    easter = easter_sunday(year)
    days = {date(year, month, day) for month, day in FIXED_HOLIDAYS}
    days.add(easter + timedelta(days=1))
    if year >= GOOD_FRIDAY_FIRST_YEAR:
        days.add(easter - timedelta(days=2))
    return frozenset(days)


def is_czech_public_holiday(day: date) -> bool:
    return day in czech_public_holidays(day.year)
