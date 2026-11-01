"""The planner prompt block: bounded, degrading, and read-only."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from deeptutor.services.planner.context import (
    MAX_CONTEXT_CHARS,
    PlannerSources,
    load_planner_context,
    render_planner_context,
)

NOW = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)  # 10:00 in Shanghai


def _sources(**overrides):
    defaults = {
        "events": lambda lo, hi, tz: [
            {"at": "11/02 19:00", "title": "组会", "task_id": None},
            {"at": "11/04 20:00", "title": "线代复习", "task_id": "t1"},
        ],
        "tasks": lambda workspace: [
            {"title": "错题重做", "due": "11/04", "id": "t1"},
            {"title": "预习第 4 章", "due": None, "id": "t2"},
        ],
        "linked_tasks": lambda workspace, session: [
            {"title": "错题重做", "due": "11/04", "id": "t1"},
        ],
        "review_due": lambda lo, hi: 12,
    }
    defaults.update(overrides)
    return PlannerSources(**defaults)


def _load(**overrides):
    kwargs = {"days": 7, "tzid": "Asia/Shanghai", "sources": _sources(), "now": NOW}
    kwargs.update(overrides)
    return load_planner_context("w-a", **kwargs)


def test_renders_all_five_facts():
    text = _load()

    assert "## 规划上下文" in text
    assert "2026-11-01" in text
    assert "Asia/Shanghai" in text
    assert "已排事件 2 个" in text and "组会" in text
    assert "未完成任务 2 个" in text and "（11/04 到期）" in text and "（无日期）" in text
    assert "本会话关联任务：错题重做" in text
    assert "间隔重复到期 12 个" in text
    assert "读取失败" not in text


def test_empty_state_says_none_without_raising():
    text = _load(
        sources=_sources(
            events=lambda lo, hi, tz: [],
            tasks=lambda workspace: [],
            linked_tasks=lambda workspace, session: [],
            review_due=lambda lo, hi: 0,
        )
    )

    assert text.count("暂无") == 3
    assert "间隔重复到期 0 个" in text


def test_one_broken_source_degrades_alone():
    def boom(lo, hi, tz):
        raise OSError("calendar file is gone")

    text = _load(sources=_sources(events=boom))

    assert "（部分数据读取失败：events）" in text
    assert "已排事件：暂无" in text
    assert "未完成任务 2 个" in text  # the other reads still ran


def test_every_broken_source_is_named():
    def boom(*args):
        raise ValueError("nope")

    text = _load(
        sources=_sources(events=boom, tasks=boom, linked_tasks=boom, review_due=boom)
    )

    assert "events" in text and "tasks" in text
    assert "linked_tasks" in text and "review_due" in text
    assert "间隔重复到期 0 个" in text


def test_workspace_is_passed_and_isolated():
    seen = {}

    def tasks(workspace):
        seen["workspace"] = workspace
        return [{"title": "w-a 的任务", "due": None, "id": "a1"}]

    text = _load(sources=_sources(tasks=tasks))

    assert seen["workspace"] == "w-a"
    assert "w-a 的任务" in text
    assert "w-b 的任务" not in text


def test_row_caps_keep_the_block_short():
    many = [{"at": f"11/02 19:00", "title": f"事件 {index}", "task_id": None} for index in range(200)]

    text = _load(sources=_sources(events=lambda lo, hi, tz: many))

    assert len(text) <= MAX_CONTEXT_CHARS
    assert text.endswith("（已截断）")
    assert "事件 199" not in text


def test_cap_trims_linked_rows_when_they_blow_the_budget():
    linked = [{"title": f"关联任务 {index} 名称填充", "due": "11/04", "id": "x"} for index in range(80)]

    text = _load(sources=_sources(linked_tasks=lambda workspace, session: linked))

    assert len(text) <= MAX_CONTEXT_CHARS
    assert text.endswith("（已截断）")


def test_long_titles_are_shortened():
    long_title = "第 3 章 特征值与特征向量 深度讲解与例题精析 完整版"

    text = _load(
        sources=_sources(tasks=lambda workspace: [{"title": long_title, "due": None, "id": "t"}])
    )

    assert long_title not in text
    assert "…" in text


def test_render_is_pure_on_a_hand_built_snapshot():
    from deeptutor.services.planner.context import PlannerSnapshot

    snapshot = PlannerSnapshot(
        today="2026-11-01 Sat", tzid="UTC", events=[], tasks=[], linked_tasks=[], review_due=3
    )

    assert render_planner_context(snapshot) == "\n".join(
        [
            "## 规划上下文",
            "今天是 2026-11-01 Sat（UTC）。",
            "- 未来 7 天已排事件：暂无",
            "- 未完成任务：暂无",
            "- 本会话关联任务：暂无",
            "- 间隔重复到期 3 个",
        ]
    )


@pytest.mark.parametrize("session", [None, "s-1"])
def test_session_id_reaches_the_linked_source(session):
    seen = {}

    def linked(workspace, wanted):
        seen["session"] = wanted
        return []

    _load(sources=_sources(linked_tasks=linked), session_id=session)

    assert seen["session"] == session


def test_default_event_source_formats_local_times_and_all_day(monkeypatch):
    """The real reader converts instants into the learner's zone, but keeps
    all-day rows on their own calendar day."""
    from types import SimpleNamespace

    from deeptutor.services.calendar import store as calendar_store_module
    from deeptutor.services.planner import context as context_module

    occurrences = [
        SimpleNamespace(all_day=False, start="2026-11-02T11:00:00Z", title="组会", task_id=None),
        SimpleNamespace(all_day=True, start="2026-11-04", title="复习日", task_id="t1"),
    ]
    monkeypatch.setattr(
        calendar_store_module,
        "get_calendar_store",
        lambda: SimpleNamespace(occurrences=lambda lo, hi, tz: {"occurrences": occurrences}),
    )

    sources = context_module._default_sources(
        workspace_id="w", session_id=None, tzid="Asia/Shanghai", moment=NOW, days=7
    )
    rows = sources.events(NOW, NOW, "Asia/Shanghai")

    assert rows[0]["at"] == "11/02 19:00"  # 11:00Z is 19:00 in Shanghai
    assert rows[0]["task_id"] is None
    assert rows[1]["at"] == "11/04 全天"
    assert rows[1]["task_id"] == "t1"
