"""Free-slot arithmetic: pure, bounded, and honest about a full day."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from zoneinfo import ZoneInfo

from deeptutor.services.planner.slots import Interval, SlotPolicy, find_free_slots

SHANGHAI = "Asia/Shanghai"
ZONE = ZoneInfo(SHANGHAI)
DAY = "2026-11-02"


def _at(clock: str, day: str = DAY, zone: ZoneInfo = ZONE) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(*(int(part) for part in day.split("-")), hour, minute, tzinfo=zone)


def _window(start="08:00", end="22:00", day=DAY) -> tuple[datetime, datetime]:
    return _at(start, day), _at(end, day)


def _total_minutes(items) -> int:
    return sum(item.minutes for item in items)


def test_a_clear_day_yields_one_window_capped_by_the_daily_ceiling():
    slots = find_free_slots([], _window(), SlotPolicy(), tzid=SHANGHAI)

    assert len(slots) == 1
    assert slots[0].start == _at("08:00")
    assert slots[0].minutes == 180  # daily_max_min


def test_busy_spans_never_leak_past_the_window():
    slots = find_free_slots(
        [Interval(_at("07:00"), _at("09:00")), Interval(_at("21:00"), _at("23:00"))],
        _window(),
        SlotPolicy(),
        tzid=SHANGHAI,
    )

    window_start, window_end = _window()
    for slot in slots:
        assert slot.start >= window_start
        assert slot.end <= window_end


def test_consecutive_blocks_keep_one_break_between_them():
    # A two-minute gap is below break_min; the earlier block gives way.
    slots = find_free_slots(
        [Interval(_at("10:00"), _at("10:02"))], _window(), SlotPolicy(), tzid=SHANGHAI
    )

    assert len(slots) >= 2
    for earlier, later in zip(slots, slots[1:]):
        gap = (later.start - earlier.end).total_seconds() / 60
        assert gap >= SlotPolicy().break_min


def test_overlapping_and_unordered_busy_is_normalized():
    busy = [
        Interval(_at("13:00"), _at("14:00")),
        Interval(_at("11:00"), _at("12:00")),
        Interval(_at("11:30"), _at("11:45")),
    ]

    slots = find_free_slots(busy, _window(), SlotPolicy(daily_max_min=600), tzid=SHANGHAI)

    assert slots == sorted(slots, key=lambda item: item.start)
    for earlier, later in zip(slots, slots[1:]):
        assert earlier.end <= later.start
    for slot in slots:
        for item in busy:
            assert slot.end <= item.start or slot.start >= item.end


def test_a_full_day_has_no_answer_and_does_not_raise():
    slots = find_free_slots(
        [Interval(_at("07:00"), _at("23:00"))], _window(), SlotPolicy(), tzid=SHANGHAI
    )

    assert slots == []


def test_the_daily_ceiling_is_per_day_not_per_call():
    policy = SlotPolicy(daily_max_min=30)
    two_days = (_at("08:00"), _at("22:00", "2026-11-03"))

    slots = find_free_slots([], two_days, policy, tzid=SHANGHAI)

    by_day: dict[str, int] = {}
    for slot in slots:
        key = slot.start.astimezone(ZONE).date().isoformat()
        by_day[key] = by_day.get(key, 0) + slot.minutes
    assert by_day == {DAY: 30, "2026-11-03": 30}


def test_fragments_below_the_granularity_are_dropped():
    policy = SlotPolicy(granularity_min=15, daily_max_min=600)
    # Leaves a 5-minute sliver between 10:00 and 10:05.
    busy = [Interval(_at("10:00"), _at("10:05"))]

    slots = find_free_slots(busy, _window(), policy, tzid=SHANGHAI)

    assert all(slot.minutes >= policy.granularity_min for slot in slots)
    assert all(slot.minutes % policy.granularity_min == 0 for slot in slots)


def test_a_dst_day_produces_no_negative_or_overlapping_windows():
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 3, 8, 0, 0, tzinfo=zone)  # spring forward
    window = (start, datetime(2026, 3, 9, 0, 0, tzinfo=zone))

    slots = find_free_slots([], window, SlotPolicy(daily_max_min=120), tzid="America/New_York")

    assert slots
    for slot in slots:
        assert slot.end > slot.start
    for earlier, later in zip(slots, slots[1:]):
        assert earlier.end <= later.start


def test_an_empty_or_backwards_window_is_empty():
    assert find_free_slots([], (_at("10:00"), _at("10:00")), SlotPolicy(), tzid=SHANGHAI) == []
    assert find_free_slots([], (_at("12:00"), _at("09:00")), SlotPolicy(), tzid=SHANGHAI) == []


def test_nothing_extends_beyond_a_single_study_day():
    policy = SlotPolicy(daily_max_min=600)
    slots = find_free_slots([], _window(), policy, tzid=SHANGHAI)

    for slot in slots:
        local = slot.start.astimezone(ZONE)
        assert local.hour >= 8
        assert slot.end.astimezone(ZONE) <= _at("22:00")


def test_results_stay_within_the_requested_window_only():
    window = (_at("10:00"), _at("12:00"))

    slots = find_free_slots([], window, SlotPolicy(daily_max_min=600), tzid=SHANGHAI)

    assert _total_minutes(slots) == 120
    assert slots[0].start == _at("10:00")
    assert slots[0].end == _at("12:00")


def test_the_day_window_edge_is_inclusive():
    slots = find_free_slots(
        [], (_at("08:00"), _at("08:15")), SlotPolicy(daily_max_min=15), tzid=SHANGHAI
    )

    assert slots and slots[0].start == _at("08:00")
    assert slots[0].minutes == 15


def test_a_window_shorter_than_one_block_has_no_answer():
    slots = find_free_slots(
        [], (_at("21:59"), _at("22:00")), SlotPolicy(daily_max_min=15), tzid=SHANGHAI
    )

    assert slots == []


def test_break_minutes_of_zero_does_not_merge_windows():
    busy = [Interval(_at("10:00"), _at("10:02"))]
    policy = SlotPolicy(daily_max_min=600, break_min=0)

    slots = find_free_slots(busy, _window(), policy, tzid=SHANGHAI)

    assert len(slots) == 2
    assert slots[0].end == _at("10:00")
    assert slots[1].start == _at("10:02")


def test_a_long_window_is_returned_whole_not_pre_sliced():
    """The planner places tasks inside a window; slicing it here would force
    every caller to reassemble what was just taken apart."""
    policy = SlotPolicy(daily_max_min=600, granularity_min=15)
    slots = find_free_slots([], _window(), policy, tzid=SHANGHAI)

    assert len(slots) == 1
    assert slots[0].minutes == 600  # the ceiling, applied once, at the end
    assert slots[0].end == _at("18:00")


def test_a_short_final_fragment_is_floored_down_to_the_granularity():
    policy = SlotPolicy(daily_max_min=600, granularity_min=30)
    busy = [Interval(_at("08:00"), _at("21:10"))]

    slots = find_free_slots(busy, _window(), policy, tzid=SHANGHAI)

    assert slots
    assert slots[0].minutes == 30  # 50 raw minutes floored to 30
    assert slots[0].end == _at("21:40")


def test_intervals_expose_their_length_in_minutes():
    item = Interval(_at("09:00"), _at("10:30"))

    assert item.minutes == 90
    assert item.end - item.start == timedelta(minutes=90)
