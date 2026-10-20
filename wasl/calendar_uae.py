"""UAE working calendar (Mon-Fri working week since 2022) with public holidays and Ramadan.

Islamic-calendar dates are APPROXIMATE (moon-sighting dependent) and are configuration: an operator
must load the official announcement each year. Used for T2/T3 SLA clocks and for synthetic attendance.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache


def _span(y: int, m: int, d: int, n: int) -> list[date]:
    return [date(y, m, d) + timedelta(days=i) for i in range(n)]


PUBLIC_HOLIDAYS: dict[date, str] = {}
for _d, _n in [
    # 2025 (approx. for Islamic dates)
    *[(x, "Eid al-Fitr") for x in _span(2025, 3, 30, 3)], (date(2025, 6, 5), "Arafat Day"),
    *[(x, "Eid al-Adha") for x in _span(2025, 6, 6, 3)], (date(2025, 6, 26), "Islamic New Year"),
    (date(2025, 9, 5), "Prophet's Birthday"), (date(2025, 1, 1), "New Year's Day"),
    (date(2025, 12, 1), "Commemoration Day"), *[(x, "National Day") for x in _span(2025, 12, 2, 2)],
    # 2026 (approx. for Islamic dates)
    (date(2026, 1, 1), "New Year's Day"), *[(x, "Eid al-Fitr") for x in _span(2026, 3, 20, 3)],
    (date(2026, 5, 26), "Arafat Day"), *[(x, "Eid al-Adha") for x in _span(2026, 5, 27, 3)],
    (date(2026, 6, 16), "Islamic New Year"), (date(2026, 8, 25), "Prophet's Birthday"),
    (date(2026, 12, 1), "Commemoration Day"), *[(x, "National Day") for x in _span(2026, 12, 2, 2)],
    # 2027 (approx.)
    (date(2027, 1, 1), "New Year's Day"), *[(x, "Eid al-Fitr") for x in _span(2027, 3, 10, 3)],
    (date(2027, 5, 15), "Arafat Day"), *[(x, "Eid al-Adha") for x in _span(2027, 5, 16, 3)],
]:
    PUBLIC_HOLIDAYS[_d] = _n

RAMADAN: list[tuple[date, date]] = [(date(2025, 3, 1), date(2025, 3, 29)), (date(2026, 2, 18), date(2026, 3, 19)),
                                    (date(2027, 2, 8), date(2027, 3, 9))]


@lru_cache(maxsize=4096)
def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in PUBLIC_HOLIDAYS  # Sat/Sun weekend


def is_ramadan(d: date) -> bool:
    return any(a <= d <= b for a, b in RAMADAN)


def holiday_name(d: date) -> str | None:
    return PUBLIC_HOLIDAYS.get(d)


def add_working_hours(start: datetime, hours: float) -> datetime:
    """Advance `hours` of clock time counting only working days (1 working day = 24 h of working calendar)."""
    t, remaining = start, timedelta(hours=hours)
    for _ in range(2000):  # bounded loop
        if remaining <= timedelta(0):
            return t
        midnight = datetime.combine(t.date() + timedelta(days=1), datetime.min.time())
        if not is_working_day(t.date()):
            t = midnight
            continue
        step = min(remaining, midnight - t)
        t += step
        remaining -= step
    return t


def next_working_slot(t: datetime, hour: int = 9) -> datetime:
    """Next clinic slot on a working day (08:00-16:00 local clinic hours, stored naive)."""
    cand = t
    if cand.hour >= 16:
        cand = datetime.combine(cand.date() + timedelta(days=1), datetime.min.time()).replace(hour=hour)
    elif cand.hour < 8:
        cand = cand.replace(hour=hour, minute=0, second=0, microsecond=0)
    while not is_working_day(cand.date()):
        cand = datetime.combine(cand.date() + timedelta(days=1), datetime.min.time()).replace(hour=hour)
    return cand
