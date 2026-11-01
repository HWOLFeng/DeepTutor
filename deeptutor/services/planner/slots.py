"""Free study time, computed instead of guessed.

A model asked "when am I free this week" will invent plausible hours, forget
the all-day block on Thursday, and answer differently every time. This module
does the arithmetic once, as a pure function: give it busy intervals and a
window, get back contiguous free windows that respect the study day, the
minimum block size and a daily ceiling.

Deliberately pure — no store, no clock, no account. Everything it needs about
"now" arrives through ``window`` and every rule arrives through ``policy``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import NamedTuple

from zoneinfo import ZoneInfo

#: One contiguous span, always timezone-aware.
class Interval(NamedTuple):
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class SlotPolicy:
    """How much study a day can hold, and in what units.

    These are product settings, not measurements: they say how a learner wants
    to be scheduled, so callers may override any of them per workspace.
    """

    daily_max_min: int = 180
    granularity_min: int = 15
    day_start: str = "08:00"
    day_end: str = "22:00"
    break_min: int = 10


def _zone(tzid: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 - an unknown zone cannot be planned in
        return ZoneInfo("UTC")


def _clock(text: str, fallback: time) -> time:
    try:
        hour, minute = str(text).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


def _normalize(busy: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    """Clips, drops and merges busy spans so the arithmetic below is simple."""
    clipped = [
        Interval(max(item.start, lo), min(item.end, hi))
        for item in busy
        if item is not None and item.end > lo and item.start < hi
    ]
    clipped = [item for item in clipped if item.end > item.start]
    clipped.sort(key=lambda item: (item.start, item.end))
    merged: list[Interval] = []
    for item in clipped:
        if merged and item.start <= merged[-1].end:
            previous = merged[-1]
            merged[-1] = Interval(previous.start, max(previous.end, item.end))
        else:
            merged.append(item)
    return merged


def _daily_windows(
    lo: datetime, hi: datetime, zone: ZoneInfo, policy: SlotPolicy
) -> list[tuple[datetime, datetime]]:
    begin = _clock(policy.day_start, time(8, 0))
    end = _clock(policy.day_end, time(22, 0))
    windows: list[tuple[datetime, datetime]] = []
    day: date = lo.astimezone(zone).date()
    last: date = hi.astimezone(zone).date()
    while day <= last:
        starts = datetime.combine(day, begin, tzinfo=zone)
        ends = datetime.combine(day, end, tzinfo=zone)
        starts, ends = max(starts, lo), min(ends, hi)
        if ends > starts:
            windows.append((starts, ends))
        day += timedelta(days=1)
    return windows


def _free_fragments(
    busy: list[Interval], window: tuple[datetime, datetime]
) -> list[Interval]:
    start, end = window
    fragments: list[Interval] = []
    cursor = start
    for item in busy:
        if item.start > cursor:
            fragments.append(Interval(cursor, min(item.start, end)))
        cursor = max(cursor, item.end)
        if cursor >= end:
            break
    if cursor < end:
        fragments.append(Interval(cursor, end))
    return [item for item in fragments if item.end > item.start]


def _floor_to_granularity(item: Interval, granularity: int) -> Interval | None:
    whole = item.minutes // granularity * granularity
    if whole < granularity:
        return None
    return Interval(item.start, item.start + timedelta(minutes=whole))


def _trim_breaks(
    items: list[Interval], break_min: int, granularity: int
) -> list[Interval]:
    """Keeps at least one break between consecutive windows on the same day.

    A trimmed window is re-floored: ``find_free_slots`` promises every window
    is a whole number of granularity steps, and a break that shortened one by
    seven minutes would quietly break that promise.
    """
    if break_min <= 0 or len(items) < 2:
        return items
    kept: list[Interval] = []
    for item in items:
        if kept:
            previous = kept[-1]
            gap = (item.start - previous.end).total_seconds() / 60
            if gap < break_min:
                shorter = previous.end - timedelta(minutes=break_min - gap)
                floored = _floor_to_granularity(
                    Interval(previous.start, shorter), granularity
                )
                if floored is not None:
                    kept[-1] = floored
        kept.append(item)
    return kept


def _under_daily_ceiling(items: list[Interval], ceiling: int, granularity: int) -> list[Interval]:
    if ceiling <= 0:
        return []
    budget = ceiling
    kept: list[Interval] = []
    for item in items:
        if budget < granularity:
            break
        if item.minutes <= budget:
            kept.append(item)
            budget -= item.minutes
            continue
        allowed = budget // granularity * granularity
        kept.append(Interval(item.start, item.start + timedelta(minutes=allowed)))
        budget -= allowed
    return kept


def find_free_slots(
    busy: list[Interval],
    window: tuple[datetime, datetime],
    policy: SlotPolicy = SlotPolicy(),
    *,
    tzid: str,
) -> list[Interval]:
    """Contiguous free windows inside ``window``, usable for study blocks.

    Windows come back in time order, never overlap each other or a busy span,
    and respect ``policy``: the local study day, the minimum block size, one
    break between consecutive blocks and a daily ceiling on total minutes.
    No solution is an empty list, never an exception.
    """
    start, end = window
    if end <= start:
        return []
    zone = _zone(tzid)
    granularity = max(1, int(policy.granularity_min))
    normalized = _normalize(list(busy), start, end)
    result: list[Interval] = []
    for day_start, day_end in _daily_windows(start, end, zone, policy):
        day_busy = _normalize(normalized, day_start, day_end)
        fragments = []
        for fragment in _free_fragments(day_busy, (day_start, day_end)):
            floored = _floor_to_granularity(fragment, granularity)
            if floored is not None:
                fragments.append(floored)
        fragments = _trim_breaks(fragments, int(policy.break_min), granularity)
        result.extend(_under_daily_ceiling(fragments, int(policy.daily_max_min), granularity))
    return result


__all__ = ["Interval", "SlotPolicy", "find_free_slots"]
