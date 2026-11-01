import asyncio

import pytest

from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.mark.asyncio
async def test_replacement_fences_delayed_original_at_reserve_and_admission(tmp_path):
    store = SQLiteSessionStore(tmp_path / "recovery.db")
    sid = await store.reserve_submission("original", "old")
    receipt = await store.replace_submission("original", "edited", "new", sid)
    assert receipt == {"status": "reserved", "session_id": sid, "submission_id": "edited"}
    with pytest.raises(RuntimeError, match="superseded"):
        await store.reserve_submission("original", "old", sid)
    with pytest.raises(RuntimeError, match="superseded"):
        await store.begin_turn(sid, submission_id="original")
    assert await store.reserve_submission("edited", "new", sid) == sid
    assert await store.replace_submission("original", "edited", "new", sid) == receipt
    with pytest.raises(RuntimeError):
        await store.replace_submission("original", "edited", "changed", sid)


@pytest.mark.asyncio
async def test_replacement_before_original_arrives_fences_it(tmp_path):
    store = SQLiteSessionStore(tmp_path / "recovery.db")
    receipt = await store.replace_submission("late", "edited", "new", None)
    with pytest.raises(RuntimeError, match="superseded"):
        await store.reserve_submission("late", "old")
    await store.begin_turn(receipt["session_id"], submission_id="edited")


@pytest.mark.asyncio
async def test_admission_replacement_race_has_one_winner(tmp_path):
    a = SQLiteSessionStore(tmp_path / "race.db")
    b = SQLiteSessionStore(tmp_path / "race.db")
    sid = await a.reserve_submission("old", "old-digest")
    admitted, replaced = await asyncio.gather(
        a.begin_turn(sid, submission_id="old"),
        b.replace_submission("old", "new", "new-digest", sid),
        return_exceptions=True,
    )
    if isinstance(admitted, Exception):
        assert "superseded" in str(admitted)
        assert replaced["status"] == "reserved"
        await b.begin_turn(sid, submission_id="new")
    else:
        assert replaced["status"] == "admitted"
        assert replaced["turn"]["id"] == admitted["id"]
        assert replaced["user_message_id"] is None


@pytest.mark.asyncio
async def test_replacement_cannot_cross_workspace_or_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from deeptutor.services.workspace import context

    scope = {"id": "one"}
    monkeypatch.setattr(
        context, "get_workspace_scope", lambda: SimpleNamespace(workspace_id=scope["id"])
    )
    store = SQLiteSessionStore(tmp_path / "scope.db")
    sid = await store.reserve_submission("old", "digest")
    other = await store.reserve_submission("other", "digest")
    with pytest.raises(RuntimeError):
        await store.replace_submission("old", "new", "new", other)
    scope["id"] = "two"
    with pytest.raises(RuntimeError):
        await store.replace_submission("old", "new", "new", sid)


@pytest.mark.asyncio
async def test_a_rejected_admission_rolls_back_its_empty_conversation(tmp_path):
    """Reserving a first submission creates a conversation; a refusal must undo it.

    Otherwise the home page lists an empty conversation at the top forever, and
    its submission id is burnt: the retry carries the same id with a request that
    was already rejected.
    """
    store = SQLiteSessionStore(tmp_path / "rollback.db")
    await store.reserve_submission("send-1", "digest")
    assert len(await store.list_sessions()) == 1
    assert await store.rollback_rejected_admission("send-1") is True
    assert await store.list_sessions() == []
    assert await store.submission_turn("send-1") is None


@pytest.mark.asyncio
async def test_a_rollback_never_removes_a_conversation_that_has_a_turn(tmp_path):
    store = SQLiteSessionStore(tmp_path / "rollback.db")
    sid = await store.reserve_submission("send-2", "digest")
    await store.begin_turn(sid, submission_id="send-2")
    assert await store.rollback_rejected_admission("send-2") is False
    assert len(await store.list_sessions()) == 1
