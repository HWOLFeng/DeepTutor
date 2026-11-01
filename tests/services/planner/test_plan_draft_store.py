"""Draft storage: the reviewable middle state between a plan and the stores."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.store import PlanDraftStore


def _items():
    return [
        PlanItemInput(kind="task", title="第 3 章 特征值", due_date="2026-11-02", duration_min=90),
        PlanItemInput(kind="task", title="错题重做", due_date="2026-11-04", duration_min=45),
        PlanItemInput(
            kind="block",
            title="特征值",
            due_date="2026-11-02",
            start_at="2026-11-02T19:00:00Z",
            duration_min=90,
            depends_on="i1",
        ),
    ]


def _create(store: PlanDraftStore, **overrides):
    payload = {
        "session_id": "s1",
        "workspace_id": "w1",
        "tzid": "Asia/Shanghai",
        "items": _items(),
        "summary": {"goal": "线代期中"},
    }
    payload.update(overrides)
    return store.create(**payload)


@pytest.fixture()
def store(tmp_path):
    return PlanDraftStore(tmp_path / "planner" / "drafts.sqlite")


def test_create_then_read_keeps_positions(store):
    draft = _create(store)

    assert draft.status == "reviewing"
    assert [item.item_id for item in draft.items] == ["i1", "i2", "i3"]
    assert [item.position for item in draft.items] == [0, 1, 2]
    assert draft.items[2].depends_on == "i1"
    assert draft.summary == {"goal": "线代期中"}
    assert store.read(draft.id) == draft


def test_expired_draft_reads_as_expired_without_raising(store):
    draft = _create(store, ttl_hours=1)
    expired_at = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.connect(write=True) as db:
        db.execute("UPDATE plan_drafts SET expires_at=? WHERE id=?", (expired_at, draft.id))

    expired = store.read(draft.id)

    assert expired.status == "expired"
    assert len(expired.items) == 3


def test_decisions_close_once_expired_or_applied(store):
    expired = _create(store, ttl_hours=1)
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.connect(write=True) as db:
        db.execute("UPDATE plan_drafts SET expires_at=? WHERE id=?", (past, expired.id))
    with pytest.raises(ValueError):
        store.set_decisions(expired.id, {"i1": "accepted"})

    applied = _create(store)
    store.mark_applied(applied.id, {"applied": 1}, "tok-1")
    with pytest.raises(ValueError):
        store.set_decisions(applied.id, {"i1": "accepted"})


def test_partial_decisions_leave_the_rest_pending(store):
    draft = _create(store)

    updated = store.set_decisions(draft.id, {"i1": "accepted", "i3": "dismissed"})

    decisions = {item.item_id: item.decision for item in updated.items}
    assert decisions == {"i1": "accepted", "i2": "pending", "i3": "dismissed"}
    assert [item.item_id for item in updated.accepted()] == ["i1"]
    assert [item.item_id for item in updated.dismissed()] == ["i3"]


def test_patch_changes_dates_but_never_the_decision(store):
    draft = _create(store)

    patched = store.patch_item(draft.id, "i1", {"due_date": "2026-11-09"})

    assert patched.items[0].due_date == "2026-11-09"
    with pytest.raises(ValueError):
        store.patch_item(draft.id, "i1", {"decision": "accepted"})
    with pytest.raises(ValueError):
        store.patch_item(draft.id, "i1", {"locked": True})
    with pytest.raises(KeyError):
        store.patch_item(draft.id, "i9", {"due_date": "2026-11-09"})


def test_mark_applied_is_idempotent(store):
    draft = _create(store)

    first, first_time = store.mark_applied(draft.id, {"applied": 2}, "tok-1")
    second, second_time = store.mark_applied(draft.id, {"applied": 99}, "tok-2")

    assert first_time is True
    assert second_time is False
    assert second.apply_result == {"applied": 2}
    assert second.undo_token == "tok-1"
    assert second.status == "applied"
    assert first.applied_at is not None


def test_undo_round_trip_marks_the_draft_undone(store):
    draft = _create(store)
    store.mark_applied(draft.id, {"applied": 2}, "tok-1")

    store.record_undo(
        "tok-1",
        draft.id,
        before={"cards": 0, "events": 0},
        after={"cards": 1, "events": 1},
        task_ids=["card-1"],
        event_ids=["event-1"],
    )
    pending = store.read_undo("tok-1")
    assert pending.undone is False
    assert pending.task_ids == ["card-1"]

    undone = store.mark_undone("tok-1")

    assert undone.undone is True
    assert store.read(draft.id).status == "undone"
    assert undone.after == {"cards": 1, "events": 1}


def test_unknown_identifiers_raise_key_error(store):
    draft = _create(store)

    with pytest.raises(KeyError):
        store.read("nope")
    with pytest.raises(KeyError):
        store.set_decisions(draft.id, {"i9": "accepted"})
    with pytest.raises(KeyError):
        store.read_undo("nope")
    with pytest.raises(KeyError):
        store.mark_applied("nope", {}, "tok")
    with pytest.raises(KeyError):
        store.mark_undone("nope")


def test_orphan_blocks_are_rejected_before_storage(store):
    with pytest.raises(ValueError):
        _create(
            store,
            items=[
                PlanItemInput(kind="block", title="孤立时间块", depends_on="i7"),
            ],
        )
    with pytest.raises(ValueError):
        _create(
            store,
            items=[
                PlanItemInput(kind="task", title="任务"),
                PlanItemInput(kind="block", title="依附任务以外的条目", depends_on="i2"),
                PlanItemInput(kind="block", title="依附另一个块", depends_on="i2"),
            ],
        )
    with pytest.raises(ValueError):
        _create(store, items=[])


def test_open_drafts_are_listed_per_session(store):
    older = _create(store)
    newer = _create(store, session_id="s1", summary={"goal": "第二版"})

    latest = store.latest_open("s1", "w1")

    assert latest is not None
    assert latest.id == newer.id
    assert store.latest_open("s2", "w1") is None

    store.set_decisions(newer.id, {"i1": "accepted"})
    store.mark_applied(newer.id, {"applied": 1}, "tok")
    assert store.latest_open("s1", "w1").id == older.id


def test_purge_drops_only_closed_expired_drafts(store):
    live = _create(store)
    stale = _create(store, ttl_hours=1)
    kept = _create(store)
    store.mark_applied(kept.id, {"applied": 1}, "tok")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.connect(write=True) as db:
        db.execute("UPDATE plan_drafts SET expires_at=? WHERE id=?", (past, stale.id))

    removed = store.purge_expired()

    assert removed == 1
    with pytest.raises(KeyError):
        store.read(stale.id)
    assert store.read(live.id).status == "reviewing"
    assert store.read(kept.id).status == "applied"
