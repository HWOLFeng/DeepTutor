"""The tasks tool: one card at a time, and only inside the active workspace."""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.services.task_board import CreateCard, TaskBoardStore, UpdateCard
from deeptutor.tools import planner_tools
from deeptutor.tools.planner_tools import TasksTool

WORKSPACE = "study"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    board = TaskBoardStore(tmp_path / "task-board" / "tasks.sqlite")
    monkeypatch.setattr(planner_tools, "_board_store", lambda: board)
    monkeypatch.setattr(planner_tools, "_workspace", lambda: WORKSPACE)
    return board


def _run(**kwargs):
    return asyncio.run(TasksTool().execute(**kwargs))


def _cards(store, workspace=WORKSPACE):
    from deeptutor.services.task_board import workspace_board

    return workspace_board(store.read(), workspace).cards


def test_definition_is_mounted_under_the_expected_name():
    tool = TasksTool()

    assert tool.name == "tasks"
    assert "tasks" in planner_tools.PLANNER_TOOL_NAMES
    actions = [p for p in tool.get_definition().parameters if p.name == "action"][0]
    assert set(actions.enum) == set(planner_tools.TASK_ACTIONS)


def test_list_shows_only_this_workspace_and_hides_archived(store):
    store.create(CreateCard(title="错题重做", due_date="2026-11-04"), WORKSPACE)
    store.create(CreateCard(title="别的空间"), "other")
    archived = store.create(CreateCard(title="已归档"), WORKSPACE)
    card = next(row for row in archived.cards if row.title == "已归档")
    store.update(card.id, UpdateCard(archived=True), workspace_id=WORKSPACE)

    result = _run(action="list")

    assert result.success is True
    assert "错题重做" in result.content
    assert "别的空间" not in result.content
    assert "已归档" not in result.content
    assert len(result.metadata["task_ids"]) == 1


def test_list_on_an_empty_board_says_so(store):
    result = _run(action="list")

    assert result.success is True
    assert result.content == "No tasks in this workspace."
    assert result.metadata["task_ids"] == []


def test_create_adds_a_card_and_reports_its_id(store):
    result = _run(action="create", title="特征值复习", due_date="2026-11-06")

    assert result.success is True
    card_id = result.metadata["task_ids"][0]
    card = next(row for row in _cards(store) if row.id == card_id)
    assert card.title == "特征值复习"
    assert card.due_date == "2026-11-06"
    assert card.status == "todo"


def test_create_without_a_due_date_leaves_it_empty(store):
    result = _run(action="create", title="随便看看", due_date="")

    card = next(row for row in _cards(store) if row.id == result.metadata["task_ids"][0])
    assert card.due_date is None


def test_create_rejects_a_bad_date_and_writes_nothing(store):
    result = _run(action="create", title="坏日期", due_date="2026-02-30")

    assert result.success is False
    assert "date" in result.content.lower()
    assert _cards(store) == []


def test_create_needs_a_title(store):
    result = _run(action="create", title="   ")

    assert result.success is False
    assert _cards(store) == []


def test_updates_apply_only_the_named_fields(store):
    created = _run(action="create", title="旧标题", due_date="2026-11-02")
    card_id = created.metadata["task_ids"][0]

    result = _run(action="update", task_id=card_id, due_date="2026-11-09")

    card = next(row for row in _cards(store) if row.id == card_id)
    assert result.success is True
    assert card.due_date == "2026-11-09"
    assert card.title == "旧标题"  # untouched


def test_update_can_clear_the_due_date(store):
    created = _run(action="create", title="有日期", due_date="2026-11-02")
    card_id = created.metadata["task_ids"][0]

    _run(action="update", task_id=card_id, due_date="")

    card = next(row for row in _cards(store) if row.id == card_id)
    assert card.due_date is None


def test_update_without_changes_fails_clearly(store):
    created = _run(action="create", title="不变")
    card_id = created.metadata["task_ids"][0]

    result = _run(action="update", task_id=card_id)

    assert result.success is False
    assert "at least one" in result.content


def test_complete_marks_the_card_done(store):
    created = _run(action="create", title="做完了")
    card_id = created.metadata["task_ids"][0]

    result = _run(action="complete", task_id=card_id)

    card = next(row for row in _cards(store) if row.id == card_id)
    assert result.success is True
    assert card.status == "done"


def test_reschedule_requires_a_date(store):
    created = _run(action="create", title="要改期")
    card_id = created.metadata["task_ids"][0]

    result = _run(action="reschedule", task_id=card_id, due_date="")

    assert result.success is False
    assert next(row for row in _cards(store) if row.id == card_id).due_date is None


def test_reschedule_moves_the_due_date(store):
    created = _run(action="create", title="要改期", due_date="2026-11-02")
    card_id = created.metadata["task_ids"][0]

    result = _run(action="reschedule", task_id=card_id, due_date="2026-11-05")

    assert result.success is True
    assert next(row for row in _cards(store) if row.id == card_id).due_date == "2026-11-05"


@pytest.mark.parametrize("action", ["update", "complete", "reschedule"])
def test_another_workspace_card_reads_as_not_found(store, action):
    other = store.create(CreateCard(title="别人的卡"), "other")
    card_id = next(row for row in other.cards if row.title == "别人的卡").id
    before = {row.id: row.model_dump() for row in _cards(store, "other")}

    result = _run(action=action, task_id=card_id, due_date="2026-11-30")

    assert result.success is False
    assert result.content == "Task not found in this workspace."
    after = {row.id: row.model_dump() for row in _cards(store, "other")}
    assert after == before


def test_a_missing_task_id_is_never_written(store):
    result = _run(action="complete", task_id="does-not-exist")

    assert result.success is False
    assert result.content == "Task not found in this workspace."


def test_unknown_action_is_rejected_with_the_valid_list(store):
    result = _run(action="archive-everything")

    assert result.success is False
    assert "Unknown action" in result.content
    assert "reschedule" in result.content


def test_the_tool_is_registered_everywhere_it_must_be():
    """A tool that is in only two of the three places silently never mounts."""
    from deeptutor.agents._shared import tool_composition
    from deeptutor.tools.builtin import CONFIGURABLE_BUILTIN_TOOL_NAMES
    from deeptutor.tools.builtin_specs import BUILTIN_TOOL_SPEC_BY_NAME

    assert "tasks" in BUILTIN_TOOL_SPEC_BY_NAME
    assert BUILTIN_TOOL_SPEC_BY_NAME["tasks"].create().name == "tasks"
    assert "tasks" in CONFIGURABLE_BUILTIN_TOOL_NAMES
    import inspect

    source = inspect.getsource(tool_composition.compose_enabled_tools)
    assert '"tasks"' in source
