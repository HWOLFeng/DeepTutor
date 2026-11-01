"""Applying a draft: written once, only what was accepted, never an orphan."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.api.routers import planner as planner_router
from deeptutor.services.calendar.store import CalendarStore
from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.store import PlanDraftStore
from deeptutor.services.task_board import TaskBoardStore

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


def _draft(env, *, workspace=WORKSPACE, include_block=True, session="s-1"):
    items = [
        PlanItemInput(kind="task", title="错题重做", due_date="2026-11-03"),
        PlanItemInput(kind="task", title="第 3 章 特征值", due_date="2026-11-04"),
    ]
    if include_block:
        items.append(
            PlanItemInput(
                kind="block",
                title="错题重做",
                due_date="2026-11-03",
                start_at="2026-11-03T08:00:00+08:00",
                duration_min=45,
                depends_on="i1",
            )
        )
    return env["drafts"].create(
        session_id=session, workspace_id=workspace, tzid=ZONE, items=items
    )


def _cards(env, workspace=WORKSPACE):
    from deeptutor.services.task_board import workspace_board

    return workspace_board(env["board"].read(), workspace).cards


def _events(env):
    return env["calendar"].occurrences(
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 12, 31, tzinfo=timezone.utc),
        ZONE,
    )["occurrences"]


def _apply(env, draft, decisions):
    return env["client"].post(
        f"/api/planner/drafts/{draft.id}/apply", json={"decisions": decisions}
    )


def test_accepting_everything_writes_the_tasks_and_the_block(env):
    draft = _draft(env)

    response = _apply(env, draft, {"i1": "accepted", "i2": "accepted", "i3": "accepted"})

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == 3
    assert len(body["task_ids"]) == 2
    assert len(body["event_ids"]) == 1
    assert body["undo_token"]
    assert {card.title for card in _cards(env)} == {"错题重做", "第 3 章 特征值"}
    events = _events(env)
    assert len(events) == 1
    block_card = next(card for card in _cards(env) if card.title == "错题重做")
    assert events[0].task_id == block_card.id  # the block belongs to the card


def test_unmentioned_and_dismissed_rows_are_not_written(env):
    draft = _draft(env)

    response = _apply(env, draft, {"i1": "accepted", "i2": "dismissed"})

    assert response.status_code == 200
    body = response.json()
    assert body["applied"] == 1
    assert body["skipped"] == 1
    assert [card.title for card in _cards(env)] == ["错题重做"]
    assert _events(env) == []


def test_rejecting_everything_writes_nothing(env):
    draft = _draft(env)

    response = _apply(env, draft, {"i1": "dismissed", "i2": "dismissed", "i3": "dismissed"})

    assert response.status_code == 200
    assert response.json()["applied"] == 0
    assert _cards(env) == []
    assert _events(env) == []


def test_an_accepted_block_without_its_task_is_refused(env):
    draft = _draft(env)
    before = (len(_cards(env)), len(_events(env)))

    response = _apply(env, draft, {"i1": "dismissed", "i3": "accepted"})

    assert response.status_code == 422
    assert "i3" in response.json()["detail"]
    assert (len(_cards(env)), len(_events(env))) == before


def test_applying_twice_writes_once(env):
    draft = _draft(env)
    first = _apply(env, draft, {"i1": "accepted", "i2": "accepted", "i3": "accepted"})
    second = _apply(env, draft, {"i1": "accepted", "i2": "accepted", "i3": "accepted"})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(_cards(env)) == 2
    assert len(_events(env)) == 1


def test_an_expired_draft_is_refused(env):
    draft = _draft(env)
    with env["drafts"].connect(write=True) as db:
        db.execute(
            "UPDATE plan_drafts SET expires_at=? WHERE id=?",
            ("2000-01-01T00:00:00Z", draft.id),
        )

    response = _apply(env, draft, {"i1": "accepted"})

    assert response.status_code == 409
    assert _cards(env) == []


def test_another_workspace_draft_reads_as_missing(env):
    foreign = _draft(env, workspace="other")

    read = env["client"].get(f"/api/planner/drafts/{foreign.id}")
    applied = _apply(env, foreign, {"i1": "accepted"})

    assert read.status_code == 404
    assert applied.status_code == 404
    assert _cards(env) == []


def test_a_duplicate_of_an_existing_card_is_merged_not_created(env):
    from deeptutor.services.task_board import CreateCard

    env["board"].create(CreateCard(title="错题重做", due_date="2026-11-03"), WORKSPACE)
    draft = _draft(env, include_block=False)

    response = _apply(env, draft, {"i1": "accepted"})

    body = response.json()
    assert body["merged"] == 1
    assert body["task_ids"] == []
    assert len(_cards(env)) == 1  # still just the original


def test_reading_a_draft_after_applying_shows_the_result(env):
    draft = _draft(env)
    applied = _apply(env, draft, {"i1": "accepted"})

    read = env["client"].get(f"/api/planner/drafts/{draft.id}")

    assert read.status_code == 200
    body = read.json()
    assert body["status"] == "applied"
    assert body["apply_result"]["undo_token"] == applied.json()["undo_token"]
    assert {item["decision"] for item in body["items"]} == {"accepted", "pending"}


def test_patching_an_item_changes_the_date_and_leaves_the_decision_alone(env):
    # Decisions are collected by the card and only sent with `apply`, so an
    # inline edit happens while the row is still pending.
    draft = _draft(env)

    response = env["client"].patch(
        f"/api/planner/drafts/{draft.id}/items/i2", json={"due_date": "2026-11-06"}
    )

    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["item_id"] == "i2")
    assert item["due_date"] == "2026-11-06"
    assert item["decision"] == "pending"


def test_an_edit_made_before_accepting_is_what_gets_written(env):
    draft = _draft(env)
    env["client"].patch(
        f"/api/planner/drafts/{draft.id}/items/i1",
        json={"title": "错题重做（改过）"},
    )

    _apply(env, draft, {"i1": "accepted"})

    assert [card.title for card in _cards(env)] == ["错题重做（改过）"]


def test_patching_a_closed_draft_is_refused(env):
    draft = _draft(env)
    _apply(env, draft, {"i1": "accepted"})

    response = env["client"].patch(
        f"/api/planner/drafts/{draft.id}/items/i2", json={"due_date": "2026-11-06"}
    )

    assert response.status_code == 400


def test_an_unknown_item_cannot_be_patched(env):
    draft = _draft(env)

    response = env["client"].patch(
        f"/api/planner/drafts/{draft.id}/items/i99", json={"due_date": "2026-11-06"}
    )

    assert response.status_code == 404


def test_a_missing_draft_is_a_404(env):
    assert env["client"].get("/api/planner/drafts/nope").status_code == 404


def test_the_apply_endpoint_is_not_a_tool():
    """The model must be unable to write the plan itself — by protocol, not
    convention."""
    import inspect

    from deeptutor.tools import planner_tools

    source = inspect.getsource(planner_tools)
    assert "def plan_week" in source or "PlanWeekTool" in source
    assert "apply_draft" not in source
    assert "ApplyRequest" not in source
