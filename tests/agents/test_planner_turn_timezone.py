"""The planner block's zone: the reader's own, when the turn names it."""

from __future__ import annotations

import pytest

from deeptutor.services.planner import context as context_module
from deeptutor.services.planner.context import build_turn_planner_context, turn_zone


@pytest.fixture()
def seen(monkeypatch):
    """Records what the builder passes on, without touching any store."""
    captured: dict[str, object] = {}

    def fake_load(workspace_id, *, session_id=None, tzid=None, **kwargs):
        captured.update(workspace_id=workspace_id, session_id=session_id, tzid=tzid)
        return "## 规划上下文"

    monkeypatch.setattr(context_module, "load_planner_context", fake_load)
    return captured


@pytest.mark.parametrize(
    "zone",
    ["America/New_York", "Asia/Shanghai", "UTC"],
)
def test_a_named_zone_reaches_the_builder(seen, zone):
    block = build_turn_planner_context(
        config={"timezone": zone}, workspace_id="w-a", session_id="s-1"
    )

    assert block == "## 规划上下文"
    assert seen["tzid"] == zone
    assert seen["workspace_id"] == "w-a"
    assert seen["session_id"] == "s-1"


@pytest.mark.parametrize(
    "config",
    [None, {}, {"timezone": ""}, {"timezone": "   "}, {"timezone": "Mars/Olympus"}],
)
def test_nothing_usable_falls_back_to_the_host(seen, config):
    build_turn_planner_context(config=config, workspace_id="w-a", session_id=None)

    # None is the signal the loader turns into local_zone_name().
    assert seen["tzid"] is None


def test_an_unusable_zone_never_raises():
    assert turn_zone({"timezone": "Mars/Olympus"}) is None
    assert turn_zone({"timezone": 42}) is None
    assert turn_zone("Asia/Shanghai") is None  # not a mapping at all


def test_a_hostile_value_is_not_repaired_into_something_else():
    assert turn_zone({"timezone": " Asia/Shanghai "}) == "Asia/Shanghai"
    assert turn_zone({"timezone": "../etc/localtime"}) is None


def test_the_block_is_returned_untouched(monkeypatch):
    marker = "## 规划上下文\n今天是 2026-11-01 Sat（America/New_York）。"
    monkeypatch.setattr(
        context_module,
        "load_planner_context",
        lambda workspace_id, *, session_id=None, tzid=None, **kwargs: marker,
    )

    assert build_turn_planner_context(config={}, workspace_id="w") == marker
