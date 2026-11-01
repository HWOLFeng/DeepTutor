"""The one rule both the router and the tools use: whose task is this?"""

from __future__ import annotations

import pytest

from deeptutor.services import task_board as task_board_module
from deeptutor.services.planner import links


def _board(cards, links_rows):
    from types import SimpleNamespace

    return SimpleNamespace(cards=cards, session_links=links_rows)


def _card(card_id: str, workspace: str, *, archived: bool = False):
    from types import SimpleNamespace

    return SimpleNamespace(id=card_id, workspace_id=workspace, archived=archived, title=card_id)


@pytest.fixture()
def board(monkeypatch):
    cards = [_card("t1", "study"), _card("t2", "other"), _card("t3", "study", archived=True)]
    monkeypatch.setattr(
        task_board_module,
        "get_task_board_store",
        lambda **kw: __import__("types").SimpleNamespace(read=lambda: None),
    )
    monkeypatch.setattr(
        task_board_module,
        "workspace_board",
        lambda raw, workspace: _board([c for c in cards if c.workspace_id == workspace], []),
    )
    return cards


def test_owned_ids_are_only_this_workspace_and_not_archived(board):
    assert links.owned_task_ids("study") == {"t1": "t1"}
    assert links.owned_task_ids("other") == {"t2": "t2"}


def test_an_owned_task_passes(board):
    links.require_owned_task("t1", "study")  # does not raise


def test_no_task_is_always_allowed(board):
    links.require_owned_task(None, "study")
    links.require_owned_task("", "study")


def test_cross_workspace_failure_looks_exactly_like_a_missing_one(board):
    with pytest.raises(ValueError) as foreign:
        links.require_owned_task("t2", "study")
    with pytest.raises(ValueError) as missing:
        links.require_owned_task("nope", "study")

    assert str(foreign.value) == str(missing.value) == "Task not found in this workspace."


def test_an_archived_task_cannot_be_scheduled(board):
    with pytest.raises(ValueError):
        links.require_owned_task("t3", "study")
