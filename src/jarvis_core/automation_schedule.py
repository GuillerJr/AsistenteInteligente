"""Finite five-field cron calendar, authored for Jarvis.

Minutes, hours, day-of-month, month and weekday accept lists, inclusive ranges,
steps and English three-letter month/weekday names. Sunday is 0 or 7. A bare
number with a step runs from that number through the field's maximum. When both
day fields restrict the calendar, either may match (the conventional cron OR).
No seconds, shell, environment, macros, random jitter or executable expression
is admitted. DST gaps are skipped and a repeated wall minute uses fold zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo


class CronRuleError(ValueError):
    """Malformed or impossible calendar rule; never contains the expression."""


_MONTHS = {name: n for n, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
)}
_DAYS = {name: n for n, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))}
_HORIZON_DAYS = 366 * 8


def _field(text: str, low: int, high: int, names: dict[str, int] | None = None) -> tuple[int, ...]:
    values = set()

    def number(word: str) -> int:
        if names and word.upper() in names:
            return names[word.upper()]
        if re.fullmatch(r"[0-9]{1,2}", word) is None:
            raise CronRuleError("cron_rule_invalid")
        value = int(word)
        if not low <= value <= high:
            raise CronRuleError("cron_rule_invalid")
        return value

    for part in text.split(","):
        pieces = part.split("/")
        if len(pieces) > 2 or not pieces[0]:
            raise CronRuleError("cron_rule_invalid")
        step = 1
        if len(pieces) == 2:
            if re.fullmatch(r"[0-9]{1,2}", pieces[1]) is None:
                raise CronRuleError("cron_rule_invalid")
            step = int(pieces[1])
            if not 1 <= step <= high - low + 1:
                raise CronRuleError("cron_rule_invalid")
        base = pieces[0]
        if base == "*":
            start, end = low, high
        elif "-" in base:
            limits = base.split("-")
            if len(limits) != 2:
                raise CronRuleError("cron_rule_invalid")
            start, end = map(number, limits)
            if start > end:
                raise CronRuleError("cron_rule_invalid")
        else:
            start = number(base)
            end = high if len(pieces) == 2 else start
        values.update(range(start, end + 1, step))
    if not values:
        raise CronRuleError("cron_rule_invalid")
    return tuple(sorted(values))


@dataclass(frozen=True)
class CronCalendar:
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: tuple[int, ...]
    day_wildcard: bool
    weekday_wildcard: bool

    def matches(self, day: date) -> bool:
        if day.month not in self.months:
            return False
        dom = day.day in self.days
        dow = (day.weekday() + 1) % 7 in self.weekdays
        if self.day_wildcard or self.weekday_wildcard:
            return dom and dow
        return dom or dow

    def instants(self, day: date, zone: ZoneInfo, *, reverse: bool = False):
        if not self.matches(day):
            return
        hours = reversed(self.hours) if reverse else self.hours
        for hour in hours:
            minutes = reversed(self.minutes) if reverse else self.minutes
            for minute in minutes:
                local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
                utc = local.astimezone(UTC)
                if utc.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
                    yield utc.timestamp()

    def next_after(self, after: float, zone: ZoneInfo) -> float:
        day = datetime.fromtimestamp(after, zone).date()
        try:
            for offset in range(_HORIZON_DAYS):
                for candidate in self.instants(day + timedelta(days=offset), zone):
                    if candidate > after:
                        return candidate
        except (OverflowError, ValueError):
            raise CronRuleError("cron_time_invalid") from None
        raise CronRuleError("cron_calendar_exhausted")

    def latest(self, due: float, now: float, zone: ZoneInfo) -> float:
        day = datetime.fromtimestamp(now, zone).date()
        try:
            for offset in range(_HORIZON_DAYS):
                for candidate in self.instants(day - timedelta(days=offset), zone, reverse=True):
                    if candidate < due:
                        return due
                    if candidate <= now:
                        return candidate
        except (OverflowError, ValueError):
            raise CronRuleError("cron_time_invalid") from None
        return due


@lru_cache(maxsize=512)
def cron_calendar(expression: str) -> CronCalendar:
    if not isinstance(expression, str) or len(expression) > 256:
        raise CronRuleError("cron_rule_invalid")
    fields = expression.split()
    if len(fields) != 5:
        raise CronRuleError("cron_rule_invalid")
    minute, hour, day, month, weekday = fields
    calendar = CronCalendar(
        _field(minute, 0, 59),
        _field(hour, 0, 23),
        _field(day, 1, 31),
        _field(month, 1, 12, _MONTHS),
        tuple(sorted({x % 7 for x in _field(weekday, 0, 7, _DAYS)})),
        day.startswith("*"),
        weekday.startswith("*"),
    )
    # One complete Gregorian 400-year cycle is unnecessary: a leap and every
    # weekday appear within eight years. Reject impossible Feb 30-style rules
    # at admission rather than stalling a polling loop after persistence.
    if not any(calendar.matches(date(2024, 1, 1) + timedelta(days=n))
               for n in range(_HORIZON_DAYS)):
        raise CronRuleError("cron_calendar_impossible")
    return calendar
