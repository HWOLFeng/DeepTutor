"""Refusals are remembered, and a reason is a tag rather than a gate."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.planner import normalize_title
from deeptutor.services.planner.store import PlanDraftStore

WEEK = date(2026, 11, 2)


def _store(tmp_path):
    return PlanDraftStore(tmp_path / "planner" / "drafts.sqlite")


def _draft(store, session="s-1", titles=("第 3 章 特征值",)):
    items = [PlanItemInput(kind="task", title=title, due_date="2026-11-04") for title in titles]
    return store.create(session_id=session, workspace_id="study", tzid="UTC", items=items)


# ── the memory ──────────────────────────────────────────────────────────


def test_a_dismissed_title_is_remembered_for_its_conversation(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)
    store.set_decisions(draft.id, {"i1": "dismissed"})

    assert store.recent_dismissed_titles("s-1") == ["第 3 章 特征值"]
    assert store.recent_dismissed_titles("s-2") == []


def test_an_accepted_title_is_not_remembered(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)
    store.set_decisions(draft.id, {"i1": "accepted"})

    assert store.recent_dismissed_titles("s-1") == []


def test_the_memory_expires_after_its_window(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)
    store.set_decisions(draft.id, {"i1": "dismissed"})
    later = datetime.now(timezone.utc) + timedelta(hours=48)

    assert store.recent_dismissed_titles("s-1") == ["第 3 章 特征值"]
    assert store.recent_dismissed_titles("s-1", now=later) == []


def test_an_empty_session_remembers_nothing(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store, session="")
    store.set_decisions(draft.id, {"i1": "dismissed"})

    assert store.recent_dismissed_titles("") == []


# ── the reason ──────────────────────────────────────────────────────────


def test_a_reason_is_stored_with_the_dismissal(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)

    updated = store.set_decisions(
        draft.id, {"i1": "dismissed"}, {"i1": "time"}
    )

    assert updated.items[0].decision == "dismissed"
    assert updated.items[0].reason == "time"


def test_a_reason_without_a_dismissal_is_refused(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)

    with pytest.raises(ValueError):
        store.set_decisions(draft.id, {"i1": "accepted"}, {"i1": "time"})


def test_an_unknown_reason_target_is_refused(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)

    with pytest.raises(KeyError):
        store.set_decisions(draft.id, {"i1": "dismissed"}, {"nope": "time"})


def test_a_blank_reason_is_the_same_as_no_reason(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)

    updated = store.set_decisions(draft.id, {"i1": "dismissed"}, {"i1": "   "})

    assert updated.items[0].reason is None


def test_a_long_reason_is_cut_not_rejected(tmp_path):
    store = _store(tmp_path)
    draft = _draft(store)

    updated = store.set_decisions(draft.id, {"i1": "dismissed"}, {"i1": "x" * 500})

    assert len(updated.items[0].reason or "") <= 200


def test_decisions_without_reasons_still_work(tmp_path):
    """The apply endpoint's old callers must keep working unchanged."""
    store = _store(tmp_path)
    draft = _draft(store)

    updated = store.set_decisions(draft.id, {"i1": "dismissed"})

    assert updated.items[0].reason is None


# ── the generator ───────────────────────────────────────────────────────


def _plan(dismissed_recent=(), titles=("第 3 章 特征值", "错题重做")):
    from deeptutor.services.planner.planner import build_plan

    return build_plan(
        goal="线代期中",
        week_start=WEEK,
        workspace_id="study",
        tzid="UTC",
        candidates=[
            PlanItemInput(kind="task", title=title, due_date="2026-11-04") for title in titles
        ],
        dismissed_recent=list(dismissed_recent),
    )


def test_a_recently_refused_row_is_not_proposed_again():
    plan = _plan(dismissed_recent=["第 3 章 特征值"])

    titles = [item.title for item in plan.items if item.kind == "task"]
    assert titles == ["错题重做"]
    assert plan.skipped_refused == 1


def test_the_refusal_match_uses_the_same_normalisation_as_duplicates():
    plan = _plan(dismissed_recent=[normalize_title(" 第3章   特征值 ")])

    titles = [item.title for item in plan.items if item.kind == "task"]
    assert titles == ["错题重做"]


def test_a_refusal_does_not_survive_a_different_title():
    plan = _plan(dismissed_recent=["第 3 章 特征值"])

    assert plan.skipped_refused == 1
    assert [item.title for item in plan.items if item.kind == "task"] == ["错题重做"]


def test_nothing_refused_means_nothing_skipped():
    plan = _plan()

    assert plan.skipped_refused == 0
    assert len([item for item in plan.items if item.kind == "task"]) == 2
