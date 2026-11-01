"""Rule application: estimates, placement, duplicates, overlap, ceiling."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.planner import (
    ESTIMATE_MINUTES,
    REVIEW_MINUTES_EACH,
    build_plan,
    normalize_title,
    week_window,
)
from deeptutor.services.planner.slots import Interval, SlotPolicy

ZONE = ZoneInfo("Asia/Shanghai")
WEEK = date(2026, 11, 2)  # a Monday


def _at(day: str, clock: str) -> datetime:
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime(*(int(part) for part in day.split("-")), hour, minute, tzinfo=ZONE)


def _plan(candidates=(), *, existing=(), busy=(), review_due=0, policy=None, week_start=WEEK):
    return build_plan(
        goal="线代期中",
        week_start=week_start,
        workspace_id="study",
        tzid="Asia/Shanghai",
        candidates=list(candidates),
        existing_tasks=list(existing),
        busy=list(busy),
        policy=policy or SlotPolicy(),
        review_due=review_due,
    )


def test_normalized_titles_ignore_case_width_and_spacing():
    assert normalize_title(" 第 3 章 特征值 ") == normalize_title("第3章特征值")
    assert normalize_title("ＡＢＣ") == normalize_title("abc")


def test_estimates_come_from_code_not_from_the_candidate():
    plan = _plan(
        [
            PlanItemInput(kind="task", title="第 3 章", due_date="2026-11-04", duration_min=5),
            PlanItemInput(kind="review", title="错题重做", due_date="2026-11-04", duration_min=500),
        ]
    )

    tasks = {item.title: item for item in plan.items if item.kind == "task"}
    assert tasks["第 3 章"].duration_min == ESTIMATE_MINUTES["task"]
    assert tasks["错题重做"].duration_min == ESTIMATE_MINUTES["review"]
    # The scheduled block may be shorter; the card still says what the work needs.
    for block in (item for item in plan.items if item.kind == "block"):
        assert block.duration_min <= ESTIMATE_MINUTES["task"]


def test_every_block_points_at_a_task_in_the_same_payload():
    plan = _plan(
        [
            PlanItemInput(kind="task", title="A", due_date="2026-11-03"),
            PlanItemInput(kind="task", title="B", due_date="2026-11-04"),
        ]
    )

    ids = [f"i{index + 1}" for index in range(len(plan.items))]
    task_ids = {
        ids[index] for index, item in enumerate(plan.items) if item.kind == "task"
    }
    blocks = [item for item in plan.items if item.kind == "block"]
    assert blocks
    for block in blocks:
        assert block.depends_on in task_ids
        assert block.depends_on in ids


def test_tasks_come_before_the_blocks_that_serve_them():
    plan = _plan([PlanItemInput(kind="task", title="A", due_date="2026-11-03")])

    kinds = [item.kind for item in plan.items]
    assert kinds == ["task", "block"]


def test_blocks_land_inside_free_time_on_the_due_day():
    busy = [Interval(_at("2026-11-04", "08:00"), _at("2026-11-04", "12:00"))]

    plan = _plan(
        [PlanItemInput(kind="task", title="特征值", due_date="2026-11-04")], busy=busy
    )

    block = next(item for item in plan.items if item.kind == "block")
    start = datetime.fromisoformat(block.start_at)
    assert start.date() == date(2026, 11, 4)
    assert start >= _at("2026-11-04", "12:00")
    assert block.duration_min == ESTIMATE_MINUTES["task"]


def test_a_fully_booked_week_keeps_the_tasks_and_drops_the_blocks():
    busy = [Interval(_at("2026-11-02", "00:00"), _at("2026-11-09", "00:00"))]

    plan = _plan(
        [PlanItemInput(kind="task", title="排不进去", due_date="2026-11-04")], busy=busy
    )

    assert [item.kind for item in plan.items] == ["task"]
    assert plan.dropped_blocks == 1


def test_a_task_can_be_scheduled_before_its_due_day_but_never_after():
    busy = [Interval(_at("2026-11-04", "07:00"), _at("2026-11-04", "23:00"))]

    plan = _plan(
        [PlanItemInput(kind="task", title="提前做", due_date="2026-11-04")], busy=busy
    )

    block = next(item for item in plan.items if item.kind == "block")
    start = datetime.fromisoformat(block.start_at)
    assert start.date() < date(2026, 11, 4)


def test_the_daily_ceiling_limits_blocks_not_tasks():
    policy = SlotPolicy(daily_max_min=60, granularity_min=15)
    candidates = [
        PlanItemInput(kind="task", title=f"任务 {index}", due_date="2026-11-03")
        for index in range(4)
    ]

    plan = _plan(candidates, policy=policy)

    blocks = [item for item in plan.items if item.kind == "block"]
    by_day: dict[str, int] = {}
    for block in blocks:
        day = datetime.fromisoformat(block.start_at).date().isoformat()
        by_day[day] = by_day.get(day, 0) + block.duration_min
    assert by_day and all(minutes <= 60 for minutes in by_day.values())
    assert len([item for item in plan.items if item.kind == "task"]) == 4


def test_a_board_duplicate_is_marked_not_hidden():
    existing = [SimpleNamespace(title="错题重做", due_date="2026-11-03", archived=False)]

    plan = _plan(
        [PlanItemInput(kind="task", title=" 错题重做 ", due_date="2026-11-03")],
        existing=existing,
    )

    task = next(item for item in plan.items if item.kind == "task")
    assert task.conflict == "duplicate"
    assert plan.duplicates == 1


def test_an_archived_card_does_not_count_as_a_duplicate():
    existing = [
        SimpleNamespace(title="错题重做", due_date="2026-11-03", archived=True),
    ]

    plan = _plan(
        [PlanItemInput(kind="task", title="错题重做", due_date="2026-11-03")],
        existing=existing,
    )

    assert next(item for item in plan.items if item.kind == "task").conflict == "none"


def test_the_same_title_twice_in_one_draft_is_dropped_once():
    plan = _plan(
        [
            PlanItemInput(kind="task", title="特征值", due_date="2026-11-04"),
            PlanItemInput(kind="task", title="特征值 ", due_date="2026-11-04"),
        ]
    )

    assert len([item for item in plan.items if item.kind == "task"]) == 1
    assert plan.duplicates == 1


def test_an_explicit_start_that_hits_an_event_is_reported_not_moved():
    busy = [Interval(_at("2026-11-04", "19:00"), _at("2026-11-04", "21:00"))]

    plan = _plan(
        [
            PlanItemInput(
                kind="task",
                title="撞车",
                due_date="2026-11-04",
                start_at="2026-11-04T19:30:00+08:00",
            )
        ],
        busy=busy,
    )

    task = next(item for item in plan.items if item.kind == "task")
    block = next(item for item in plan.items if item.kind == "block")
    assert task.conflict == "overlap"
    assert datetime.fromisoformat(block.start_at).hour == 19
    assert plan.overlaps == 1


def test_due_reviews_arrive_as_one_locked_row():
    plan = _plan([], review_due=12)

    assert len(plan.items) == 1
    review = plan.items[0]
    assert review.kind == "review"
    assert review.source == "srs"
    assert review.locked is True
    assert review.duration_min == min(12 * REVIEW_MINUTES_EACH, SlotPolicy().daily_max_min)


def test_no_reviews_means_no_review_row():
    assert _plan([], review_due=0).items == []


def test_block_proposals_from_the_model_are_ignored():
    plan = _plan(
        [
            PlanItemInput(kind="block", title="模型自己放的时间块", due_date="2026-11-04"),
            PlanItemInput(kind="task", title="真任务", due_date="2026-11-04"),
        ]
    )

    titles = [item.title for item in plan.items]
    assert "模型自己放的时间块" not in titles


def test_a_due_date_outside_the_week_is_pulled_into_it():
    plan = _plan([PlanItemInput(kind="task", title="下个月", due_date="2026-12-20")])

    task = next(item for item in plan.items if item.kind == "task")
    assert task.due_date == WEEK.isoformat()


def test_the_same_input_produces_the_same_plan():
    candidates = [
        PlanItemInput(kind="task", title="B", due_date="2026-11-04"),
        PlanItemInput(kind="task", title="A", due_date="2026-11-03"),
    ]

    first = _plan(candidates).items
    second = _plan(candidates).items

    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]
    assert [item.title for item in first if item.kind == "task"] == ["A", "B"]


def test_the_window_starts_on_monday_in_the_readers_zone():
    start, end = week_window(WEEK, "Asia/Shanghai")

    assert start == _at("2026-11-02", "00:00")
    assert end == _at("2026-11-09", "00:00")


def test_items_survive_the_draft_store_unchanged():
    """The store validates ``depends_on``; a generator that names an id the
    store will not assign would fail there, not here."""
    from deeptutor.services.planner.store import PlanDraftStore

    plan = _plan(
        [
            PlanItemInput(kind="task", title="A", due_date="2026-11-03"),
            PlanItemInput(kind="task", title="B", due_date="2026-11-04"),
        ],
        review_due=3,
    )

    import tempfile
    from pathlib import Path

    store = PlanDraftStore(Path(tempfile.mkdtemp()) / "drafts.sqlite")
    draft = store.create(
        session_id="s1", workspace_id="study", tzid="Asia/Shanghai", items=plan.items
    )

    assert [item.kind for item in draft.items] == [item.kind for item in plan.items]
    for item in draft.items:
        if item.kind == "block":
            target = next(row for row in draft.items if row.item_id == item.depends_on)
            assert target.kind == "task"


@pytest.mark.parametrize("kind", ["task", "review"])
def test_estimates_are_positive_for_every_kind(kind):
    assert ESTIMATE_MINUTES[kind] > 0
