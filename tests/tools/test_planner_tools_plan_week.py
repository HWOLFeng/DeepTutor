"""plan_week: it produces a draft, and it writes nothing else."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from deeptutor.services.calendar.store import CalendarStore
from deeptutor.services.planner.store import PlanDraftStore
from deeptutor.services.task_board import CreateCard, TaskBoardStore
from deeptutor.tools import planner_tools
from deeptutor.tools.planner_tools import PlanWeekTool

WORKSPACE = "study"
ZONE = "Asia/Shanghai"
WEEK = "2026-11-02"  # Monday


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    board = TaskBoardStore(tmp_path / "task-board" / "tasks.sqlite")
    calendar = CalendarStore(tmp_path / "calendar" / "calendar.sqlite")
    drafts = PlanDraftStore(tmp_path / "planner" / "drafts.sqlite")
    monkeypatch.setattr(planner_tools, "_board_store", lambda: board)
    monkeypatch.setattr(planner_tools, "_calendar_store", lambda: calendar)
    monkeypatch.setattr(planner_tools, "_draft_store", lambda: drafts)
    monkeypatch.setattr(planner_tools, "_workspace", lambda: WORKSPACE)
    monkeypatch.setattr(planner_tools, "_review_due", lambda horizon: 0)
    return {"board": board, "calendar": calendar, "drafts": drafts}


def _run(**kwargs):
    kwargs.setdefault("goal", "线代期中")
    kwargs.setdefault("week_start", WEEK)
    kwargs.setdefault("tzid", ZONE)
    kwargs.setdefault("_plan_session", "s-1")
    kwargs.setdefault("_plan_language", "zh")
    return asyncio.run(PlanWeekTool().execute(**kwargs))


def _counts(stores) -> tuple[int, int]:
    cards = len(stores["board"].read().cards) if stores["board"].path.exists() else 0
    occurrences = stores["calendar"].occurrences(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 31, tzinfo=timezone.utc),
        ZONE,
    )["occurrences"]
    return cards, len(occurrences)


def _draft(stores, result):
    return stores["drafts"].read(result.metadata["plan_draft_id"])


def test_it_creates_a_reviewable_draft_and_writes_nothing_else(stores):
    before = _counts(stores)

    result = _run(
        items=[
            {"title": "第 3 章 特征值", "due_date": "2026-11-04"},
            {"title": "错题重做", "due_date": "2026-11-03"},
        ]
    )

    assert result.success is True
    draft = _draft(stores, result)
    assert draft.status == "reviewing"
    assert draft.workspace_id == WORKSPACE
    assert draft.session_id == "s-1"
    kinds = [item.kind for item in draft.items]
    assert kinds.count("task") == 2
    assert kinds.count("block") >= 1
    assert _counts(stores) == before  # the invariant: no card, no event


def test_the_model_cannot_set_a_duration(stores):
    from deeptutor.services.planner.planner import ESTIMATE_MINUTES

    result = _run(items=[{"title": "特征值", "due_date": "2026-11-04", "duration_min": 5}])

    draft = _draft(stores, result)
    task = next(item for item in draft.items if item.kind == "task")
    assert task.duration_min == ESTIMATE_MINUTES["task"]


def test_reviews_due_arrive_as_one_locked_row(stores, monkeypatch):
    monkeypatch.setattr(planner_tools, "_review_due", lambda horizon: 9)

    result = _run()

    draft = _draft(stores, result)
    assert [item.kind for item in draft.items] == ["review"]
    review = draft.items[0]
    assert review.locked is True
    assert review.source == "srs"
    assert "9" in review.title


def test_nothing_to_plan_is_a_clear_failure_and_no_draft(stores):
    result = _run()

    assert result.success is False
    assert "Nothing to plan" in result.content
    assert "plan_draft_id" not in result.metadata


def test_the_draft_records_the_goal_and_the_week(stores):
    result = _run(items=[{"title": "A", "due_date": "2026-11-05"}], daily_max_min=90)

    draft = _draft(stores, result)
    assert draft.summary["goal"] == "线代期中"
    assert draft.summary["week_start"] == WEEK
    assert draft.summary["daily_max_min"] == 90
    assert draft.tzid == ZONE


def test_daily_max_min_is_clamped_to_a_sane_range(stores):
    result = _run(items=[{"title": "A", "due_date": "2026-11-05"}], daily_max_min=5)

    draft = _draft(stores, result)
    assert draft.summary["daily_max_min"] == planner_tools.PLAN_MIN_DAILY_MIN


def test_a_bad_week_start_is_rejected(stores):
    result = _run(week_start="next monday", items=[{"title": "A"}])

    assert result.success is False
    assert "YYYY-MM-DD" in result.content


def test_a_missing_goal_is_rejected(stores):
    result = _run(goal="  ", items=[{"title": "A"}])

    assert result.success is False
    assert "goal" in result.content


def test_candidates_without_titles_are_skipped(stores):
    result = _run(items=[{"title": "  "}, {"title": "留下来的", "due_date": "2026-11-04"}])

    draft = _draft(stores, result)
    titles = [item.title for item in draft.items if item.kind == "task"]
    assert titles == ["留下来的"]


def test_the_tool_tells_the_model_not_to_create_what_it_proposed(stores):
    result = _run(items=[{"title": "A", "due_date": "2026-11-04"}])

    assert "Nothing is written" in result.content
    assert "do not create" in result.content


def test_an_existing_card_on_the_same_day_is_flagged(stores):
    from deeptutor.services.planner.planner import normalize_title

    stores["board"].create(CreateCard(title="错题重做", due_date="2026-11-03"), WORKSPACE)

    result = _run(items=[{"title": " 错题重做 ", "due_date": "2026-11-03"}])

    draft = _draft(stores, result)
    task = next(item for item in draft.items if item.kind == "task")
    assert task.conflict == "duplicate"
    assert normalize_title(task.title) == normalize_title("错题重做")


def test_blocks_always_point_at_a_task_in_the_draft(stores):
    result = _run(
        items=[
            {"title": "A", "due_date": "2026-11-03"},
            {"title": "B", "due_date": "2026-11-04"},
            {"title": "C", "due_date": "2026-11-05"},
        ]
    )

    draft = _draft(stores, result)
    by_id = {item.item_id: item for item in draft.items}
    for item in draft.items:
        if item.kind == "block":
            assert by_id[item.depends_on].kind == "task"


def test_busy_calendar_time_is_respected(stores):
    result = _run(items=[{"title": "A", "due_date": "2026-11-04"}])
    draft = _draft(stores, result)
    block = next(item for item in draft.items if item.kind == "block")

    # Book that whole day, then plan again: no block may land on it.
    from deeptutor.services.calendar.models import EventInput

    stores["calendar"].create(
        EventInput(
            title="全天占用",
            all_day=True,
            start="2026-11-04",
            end="2026-11-05",
            tzid=ZONE,
        )
    )
    second = _run(items=[{"title": "A2", "due_date": "2026-11-04"}])
    draft2 = _draft(stores, second)
    blocks = [item for item in draft2.items if item.kind == "block"]
    for item in blocks:
        assert not item.start_at.startswith("2026-11-04")
    assert block.start_at  # the first plan had somewhere to go


def test_the_tool_is_registered_everywhere_it_must_be():
    import inspect

    from deeptutor.agents._shared import tool_composition
    from deeptutor.tools.builtin import CONFIGURABLE_BUILTIN_TOOL_NAMES
    from deeptutor.tools.builtin_specs import BUILTIN_TOOL_SPEC_BY_NAME

    assert "plan_week" in BUILTIN_TOOL_SPEC_BY_NAME
    assert BUILTIN_TOOL_SPEC_BY_NAME["plan_week"].create().name == "plan_week"
    assert "plan_week" in CONFIGURABLE_BUILTIN_TOOL_NAMES
    assert '"plan_week"' in inspect.getsource(tool_composition.compose_enabled_tools)


def test_the_session_comes_from_the_server_not_the_model():
    import inspect

    from deeptutor.agents.loop import pipeline

    source = inspect.getsource(pipeline)
    assert 'kwargs["_plan_session"] = context.session_id' in source
