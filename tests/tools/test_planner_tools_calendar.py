"""The calendar tool: one event per call, and only this workspace's tasks."""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.services.calendar.store import CalendarStore
from deeptutor.services.planner import links as links_module
from deeptutor.tools import planner_tools
from deeptutor.tools.planner_tools import CalendarTool

WORKSPACE = "study"
ZONE = "Asia/Shanghai"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    calendar = CalendarStore(tmp_path / "calendar" / "calendar.sqlite")
    monkeypatch.setattr(planner_tools, "_calendar_store", lambda: calendar)
    monkeypatch.setattr(planner_tools, "_workspace", lambda: WORKSPACE)
    monkeypatch.setattr(links_module, "owned_task_ids", lambda workspace: {"t1": "特征值复习"})
    return calendar


def _run(**kwargs):
    kwargs.setdefault("tzid", ZONE)
    return asyncio.run(CalendarTool().execute(**kwargs))


def _events(store):
    return store.occurrences(
        __import__("datetime").datetime(2026, 11, 1, tzinfo=__import__("datetime").timezone.utc),
        __import__("datetime").datetime(2026, 11, 9, tzinfo=__import__("datetime").timezone.utc),
        ZONE,
    )["occurrences"]


def test_definition_is_mounted_under_the_expected_name():
    tool = CalendarTool()

    assert tool.name == "calendar"
    assert "calendar" in planner_tools.PLANNER_TOOL_NAMES
    action = [p for p in tool.get_definition().parameters if p.name == "action"][0]
    assert set(action.enum) == set(planner_tools.CALENDAR_ACTIONS)


def test_create_then_list_round_trips_the_event(store):
    created = _run(
        action="create_event",
        title="组会",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )
    listed = _run(
        action="list_events", start="2026-11-01T00:00:00+08:00", end="2026-11-08T00:00:00+08:00"
    )

    assert created.success is True
    assert listed.success is True
    assert "组会" in listed.content
    assert len(_events(store)) == 1


def test_create_needs_a_title(store):
    result = _run(
        action="create_event",
        title="  ",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )

    assert result.success is False
    assert _events(store) == []


def test_an_end_before_the_start_is_rejected(store):
    result = _run(
        action="create_event",
        title="倒着来",
        start="2026-11-02T20:30:00+08:00",
        end="2026-11-02T19:00:00+08:00",
    )

    assert result.success is False
    assert _events(store) == []


def test_all_day_events_take_dates_with_an_exclusive_end(store):
    created = _run(action="create_event", title="复习日", all_day=True, start="2026-11-03", end="2026-11-04")

    assert created.success is True
    row = _events(store)[0]
    assert row.all_day is True
    assert row.start == "2026-11-03"
    assert row.end == "2026-11-04"


def test_a_task_from_another_workspace_is_refused(store):
    result = _run(
        action="create_event",
        title="不属于这里的任务",
        task_id="t999",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:00:00+08:00",
    )

    assert result.success is False
    assert result.content == "Task not found in this workspace."
    assert _events(store) == []


def test_a_time_block_for_an_owned_task_is_recorded_with_the_link(store):
    result = _run(
        action="create_event",
        title="特征值",
        task_id="t1",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )

    assert result.success is True
    row = _events(store)[0]
    assert row.task_id == "t1"
    assert row.task_workspace_id == WORKSPACE


def test_find_free_slots_avoids_existing_events(store):
    _run(
        action="create_event",
        title="组会",
        start="2026-11-02T10:00:00+08:00",
        end="2026-11-02T12:00:00+08:00",
    )

    result = _run(
        action="find_free_slots", start="2026-11-02T08:00:00+08:00", end="2026-11-02T22:00:00+08:00"
    )

    assert result.success is True
    from datetime import datetime

    busy_lo = datetime.fromisoformat("2026-11-02T10:00:00+08:00")
    busy_hi = datetime.fromisoformat("2026-11-02T12:00:00+08:00")
    for start, end in result.metadata["slots"]:
        span_lo, span_hi = datetime.fromisoformat(start), datetime.fromisoformat(end)
        assert span_hi <= busy_lo or span_lo >= busy_hi


def test_an_all_day_event_blocks_only_its_own_day(store):
    _run(action="create_event", title="全天复习", all_day=True, start="2026-11-03", end="2026-11-04")

    result = _run(
        action="find_free_slots", start="2026-11-02T08:00:00+08:00", end="2026-11-04T22:00:00+08:00"
    )

    days = {start[:10] for start, _end in result.metadata["slots"]}
    assert "2026-11-02" in days
    assert "2026-11-03" not in days  # the all-day row owns that whole day
    assert "2026-11-04" in days


def test_update_moves_an_event_and_needs_both_ends(store):
    created = _run(
        action="create_event",
        title="组会",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )
    event_id = created.metadata["event_ids"][0]

    half = _run(action="update_event", event_id=event_id, start="2026-11-02T21:00:00+08:00")
    assert half.success is False
    assert "both start and end" in half.content

    moved = _run(
        action="update_event",
        event_id=event_id,
        title="组会（改期）",
        start="2026-11-02T21:00:00+08:00",
        end="2026-11-02T22:00:00+08:00",
    )
    assert moved.success is True
    row = _events(store)[0]
    assert row.title == "组会（改期）"


def test_update_without_changes_fails_clearly(store):
    created = _run(
        action="create_event",
        title="组会",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )

    # No tzid here on purpose: a lone zone is not a content change, and the
    # rejection must happen before anything reaches the store.
    result = asyncio.run(
        CalendarTool().execute(action="update_event", event_id=created.metadata["event_ids"][0])
    )

    assert result.success is False
    assert "at least one field" in result.content


def test_delete_removes_the_event_and_an_unknown_id_is_a_soft_failure(store):
    created = _run(
        action="create_event",
        title="组会",
        start="2026-11-02T19:00:00+08:00",
        end="2026-11-02T20:30:00+08:00",
    )
    event_id = created.metadata["event_ids"][0]

    deleted = _run(action="delete_event", event_id=event_id)
    missing = _run(action="delete_event", event_id=event_id)

    assert deleted.success is True
    assert _events(store) == []
    assert missing.success is False
    assert "not found" in missing.content.lower()


def test_unknown_action_lists_the_valid_ones(store):
    result = _run(action="make-it-friday")

    assert result.success is False
    assert "find_free_slots" in result.content


def test_a_short_window_has_no_free_time(store):
    result = _run(
        action="find_free_slots",
        start="2026-11-02T21:59:00+08:00",
        end="2026-11-02T22:00:00+08:00",
    )

    assert result.success is True
    assert result.content == "No free study time in that window."


def test_a_bad_zone_fails_loudly_instead_of_shifting_times(store):
    result = asyncio.run(
        CalendarTool().execute(
            action="list_events",
            start="2026-11-01T00:00:00+08:00",
            end="2026-11-08T00:00:00+08:00",
            tzid="Mars/Olympus",
        )
    )

    assert result.success is False


def test_the_tool_is_registered_everywhere_it_must_be():
    import inspect

    from deeptutor.agents._shared import tool_composition
    from deeptutor.tools.builtin import CONFIGURABLE_BUILTIN_TOOL_NAMES
    from deeptutor.tools.builtin_specs import BUILTIN_TOOL_SPEC_BY_NAME

    assert "calendar" in BUILTIN_TOOL_SPEC_BY_NAME
    assert BUILTIN_TOOL_SPEC_BY_NAME["calendar"].create().name == "calendar"
    assert "calendar" in CONFIGURABLE_BUILTIN_TOOL_NAMES
    assert '"calendar"' in inspect.getsource(tool_composition.compose_enabled_tools)
