"""Undoing an applied batch: take back what this batch wrote, nothing else."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.api.routers import planner as planner_router
from deeptutor.services.calendar.store import CalendarStore
from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.store import PlanDraftStore
from deeptutor.services.task_board import CreateCard, TaskBoardStore, UpdateCard

WORKSPACE = "study"
ZONE = "Asia/Shanghai"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    board = TaskBoardStore(tmp_path / "task-board" / "tasks.sqlite")
    calendar = CalendarStore(tmp_path / "calendar" / "calendar.sqlite")
    drafts = PlanDraftStore(tmp_path / "planner" / "drafts.sqlite")
    monkeypatch.setattr(planner_router, "get_task_board_store", lambda **kw: board)
    monkeypatch.setattr(planner_router, "get_calendar_store", lambda: calendar)
    monkeypatch.setattr(planner_router, "get_plan_draft_store", lambda: drafts)
    monkeypatch.setattr(planner_router, "board_workspace", lambda request: WORKSPACE)
    app = FastAPI()
    app.include_router(planner_router.router, prefix="/api/planner")
    return {
        "client": TestClient(app),
        "board": board,
        "calendar": calendar,
        "drafts": drafts,
    }


def _draft(env, *, workspace=WORKSPACE):
    items = [
        PlanItemInput(kind="task", title="错题重做", due_date="2026-11-03"),
        PlanItemInput(kind="task", title="第 3 章 特征值", due_date="2026-11-04"),
        PlanItemInput(
            kind="block",
            title="错题重做",
            due_date="2026-11-03",
            start_at="2026-11-03T08:00:00+08:00",
            duration_min=45,
            depends_on="i1",
        ),
    ]
    return env["drafts"].create(
        session_id="s-1", workspace_id=workspace, tzid=ZONE, items=items
    )


def _apply(env, draft):
    return env["client"].post(
        f"/api/planner/drafts/{draft.id}/apply",
        json={"decisions": {"i1": "accepted", "i2": "accepted", "i3": "accepted"}},
    )


def _cards(env):
    from deeptutor.services.task_board import workspace_board

    return workspace_board(env["board"].read(), WORKSPACE).cards


def _events(env):
    return env["calendar"].occurrences(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 31, tzinfo=timezone.utc),
        ZONE,
    )["occurrences"]


def test_undo_takes_the_batch_back_and_reports_what_went(env):
    draft = _draft(env)
    applied = _apply(env, draft).json()
    assert len(_cards(env)) == 2 and len(_events(env)) == 1

    response = env["client"].post(f"/api/planner/undo/{applied['undo_token']}")

    assert response.status_code == 200
    body = response.json()
    assert body["removed"] == 3
    assert body["skipped"] == []
    assert _cards(env) == []
    assert _events(env) == []


def test_a_row_the_learner_edited_stays_and_is_named(env):
    draft = _draft(env)
    applied = _apply(env, draft).json()
    card = next(row for row in _cards(env) if row.title == "错题重做")
    env["board"].update(card.id, UpdateCard(title="错题重做（我自己改的）"), workspace_id=WORKSPACE)

    body = env["client"].post(f"/api/planner/undo/{applied['undo_token']}").json()

    assert body["removed"] == 2
    assert body["skipped"] == ["错题重做（我自己改的）"]
    remaining = [row.title for row in _cards(env)]
    assert remaining == ["错题重做（我自己改的）"]


def test_a_moved_event_is_not_deleted(env):
    from deeptutor.services.calendar.models import EventPatch, EventPatchRequest

    draft = _draft(env)
    applied = _apply(env, draft).json()
    event = _events(env)[0]
    env["calendar"].update(
        event.event_id,
        EventPatchRequest(
            changes=EventPatch(start="2026-11-03T10:00:00+08:00", end="2026-11-03T10:45:00+08:00")
        ),
    )

    body = env["client"].post(f"/api/planner/undo/{applied['undo_token']}").json()

    assert body["skipped"] == [event.title]
    assert len(_events(env)) == 1


def test_undoing_twice_is_a_no_op(env):
    draft = _draft(env)
    applied = _apply(env, draft).json()
    first = env["client"].post(f"/api/planner/undo/{applied['undo_token']}").json()
    second = env["client"].post(f"/api/planner/undo/{applied['undo_token']}").json()

    assert first["removed"] == 3
    assert second["already_undone"] is True
    assert second["removed"] == 0


def test_an_unknown_token_is_a_404(env):
    assert env["client"].post("/api/planner/undo/nope").status_code == 404


def test_undo_closes_the_draft(env):
    draft = _draft(env)
    applied = _apply(env, draft).json()

    env["client"].post(f"/api/planner/undo/{applied['undo_token']}")

    body = env["client"].get(f"/api/planner/drafts/{draft.id}").json()
    assert body["status"] == "undone"
    again = env["client"].post(
        f"/api/planner/drafts/{draft.id}/apply",
        json={"decisions": {"i1": "accepted"}},
    )
    assert again.status_code == 409


def test_a_merged_card_is_never_deleted_by_undo(env):
    env["board"].create(CreateCard(title="错题重做", due_date="2026-11-03"), WORKSPACE)
    draft = _draft(env)
    applied = _apply(env, draft).json()

    body = env["client"].post(f"/api/planner/undo/{applied['undo_token']}").json()

    # The learner's own card was merged into, not created: it stays.
    assert [row.title for row in _cards(env)] == ["错题重做"]
    assert body["removed"] == 2  # the other task and the block


def test_another_workspace_cannot_undo_this_batch(env):
    other = _draft(env, workspace="other")
    applied = _apply(env, other)
    assert applied.status_code == 404  # the draft itself is not visible there


def test_deleting_a_card_takes_its_conversation_links_with_it(tmp_path):
    from deeptutor.services.task_board import LinkTasks, workspace_board

    board = TaskBoardStore(tmp_path / "tasks.sqlite")
    made = board.create(CreateCard(title="有链接的卡"), WORKSPACE)
    card = next(row for row in workspace_board(made, WORKSPACE).cards)
    board.link_tasks("s-1", LinkTasks(workspace_id=WORKSPACE, task_ids=[card.id]))

    board.delete(card.id, workspace_id=WORKSPACE)

    assert workspace_board(board.read(), WORKSPACE).cards == []
    # The session row may survive with an empty list; what must not survive is
    # a link naming a card that no longer exists.
    assert all(card.id not in link.task_ids for link in board.read().session_links)


def test_deleting_a_card_from_another_workspace_is_a_no_op(tmp_path):
    board = TaskBoardStore(tmp_path / "tasks.sqlite")
    located = board.create(CreateCard(title="别的空间"), "other")
    card = next(row for row in located.cards if row.title == "别的空间")

    with pytest.raises(KeyError):
        board.delete(card.id, workspace_id=WORKSPACE)

    assert [row.id for row in board.read().cards] == [card.id]
