"""Planning tools for ordinary chat: the task board, the calendar, the draft.

Chat could already talk about a plan but had no way to see one through: the
board was read-only to the model and the calendar was invisible. These tools
close that gap in the narrowest form that works — one card at a time for
``tasks`` and ``calendar``, and a reviewable draft for ``plan_week`` (added
with the planner router). None of them can bypass the learner's review.

Two rules keep them safe to mount permanently:

**They never leave the workspace.** No tool accepts a workspace parameter; the
active workspace is resolved per call, and an id from another workspace reads
as "not found" rather than revealing that it exists.

**They never batch.** One card, one event per call. Bulk planning is
``plan_week``'s job, and it produces a draft the learner accepts item by item.
"""

from __future__ import annotations

import json
from typing import Any

from deeptutor.core.tool_protocol import (
    BaseTool,
    ToolDefinition,
    ToolParameter,
    ToolResult,
)
from deeptutor.tools.prompting import load_prompt_hints

TASKS_TOOL_NAME = "tasks"
PLANNER_TOOL_NAMES: tuple[str, ...] = (TASKS_TOOL_NAME,)

TASK_ACTIONS = ("list", "create", "update", "complete", "reschedule")


class _PromptHintsMixin:
    def get_prompt_hints(self, language: str = "en"):
        return load_prompt_hints(self.name, language=language)


def _board_store():
    """Indirection so tests can stand in for the account store."""
    from deeptutor.services.task_board import get_task_board_store

    return get_task_board_store()


def _workspace() -> str:
    from deeptutor.services.workspace.context import current_workspace_id

    return current_workspace_id()


def _board(store, workspace_id: str):
    from deeptutor.services.task_board import workspace_board

    return workspace_board(store.read(), workspace_id)


def _open_cards(store, workspace_id: str):
    cards = [card for card in _board(store, workspace_id).cards if not card.archived]
    cards.sort(key=lambda card: (card.status == "done", card.due_date or "9999-12-31", card.created_at))
    return cards


def _due_date(raw: Any) -> str | None:
    text = str(raw or "").strip()
    return text or None


def _failure(message: str) -> ToolResult:
    return ToolResult(content=message, success=False, metadata={"task_ids": []})


def _describe(card) -> str:
    parts = [f"`{card.id}`", card.title]
    if card.due_date:
        parts.append(f"due {card.due_date}")
    parts.append(card.status)
    if card.archived:
        parts.append("archived")
    return "- " + " · ".join(parts)


class TasksTool(_PromptHintsMixin, BaseTool):
    """Read and edit the current workspace's task cards."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=TASKS_TOOL_NAME,
            description=(
                "Read and edit the learner's task cards in the active workspace. "
                "Use `list` before proposing changes, `create` for a new task, "
                "`update` to change its title/note/due date, `complete` when it is "
                "done, and `reschedule` to move a due date. One card per call. "
                "Dates are plain calendar days: YYYY-MM-DD. This never touches the "
                "calendar — a time block for a task is a calendar event."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="What to do with the task board.",
                    enum=list(TASK_ACTIONS),
                ),
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="Target card id (from `list`). Required except for list/create.",
                    required=False,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Card title (create: required; update: new title).",
                    required=False,
                ),
                ToolParameter(
                    name="note",
                    type="string",
                    description="Free-form note. Empty string clears it.",
                    required=False,
                ),
                ToolParameter(
                    name="due_date",
                    type="string",
                    description="Due date as YYYY-MM-DD. Empty string clears it; required for reschedule.",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip().lower()
        if action not in TASK_ACTIONS:
            return _failure(
                f"Unknown action {action!r}. Use one of: {', '.join(TASK_ACTIONS)}."
            )
        try:
            workspace_id = _workspace()
            store = _board_store()
            if action == "list":
                return self._list(store, workspace_id)
            if action == "create":
                return self._create(store, workspace_id, kwargs)
            task_id = str(kwargs.get("task_id") or "").strip()
            if not task_id:
                return _failure(f"{action} needs a task_id (see action='list').")
            if action == "update":
                return self._update(store, workspace_id, task_id, kwargs)
            if action == "complete":
                return self._set_status(store, workspace_id, task_id)
            return self._reschedule(store, workspace_id, task_id, kwargs)
        except KeyError:
            # Another workspace's card must read exactly like a missing one.
            return _failure("Task not found in this workspace.")
        except ValueError as exc:
            return _failure(str(exc))

    def _list(self, store, workspace_id: str) -> ToolResult:
        cards = _open_cards(store, workspace_id)
        if not cards:
            return ToolResult(
                content="No tasks in this workspace.",
                metadata={"task_ids": []},
            )
        open_count = sum(1 for card in cards if card.status != "done")
        lines = [f"{open_count} open task(s) in this workspace:"]
        lines.extend(_describe(card) for card in cards)
        return ToolResult(
            content="\n".join(lines),
            metadata={"task_ids": [card.id for card in cards]},
        )

    def _create(self, store, workspace_id: str, kwargs: dict[str, Any]) -> ToolResult:
        from deeptutor.services.task_board import CreateCard

        title = str(kwargs.get("title") or "").strip()
        if not title:
            return _failure("create needs a title.")
        payload = CreateCard(
            title=title,
            note=str(kwargs.get("note") or ""),
            due_date=_due_date(kwargs.get("due_date")),
        )
        known = {card.id for card in _board(store, workspace_id).cards}
        board = store.create(payload, workspace_id)
        created = [card for card in board.cards if card.id not in known]
        if not created:
            return _failure("The task board did not accept the new card.")
        card = created[0]
        return ToolResult(
            content=f"Created {_describe(card)[2:]}",
            metadata={"task_ids": [card.id]},
        )

    def _update(
        self, store, workspace_id: str, task_id: str, kwargs: dict[str, Any]
    ) -> ToolResult:
        from deeptutor.services.task_board import UpdateCard

        changes: dict[str, Any] = {}
        if "title" in kwargs and str(kwargs.get("title") or "").strip():
            changes["title"] = str(kwargs["title"]).strip()
        if "note" in kwargs and kwargs.get("note") is not None:
            changes["note"] = str(kwargs["note"])
        if "due_date" in kwargs and kwargs.get("due_date") is not None:
            changes["due_date"] = _due_date(kwargs["due_date"])
        if not changes:
            return _failure("update needs at least one of: title, note, due_date.")
        board = store.update(task_id, UpdateCard(**changes), workspace_id=workspace_id)
        card = next((row for row in board.cards if row.id == task_id), None)
        if card is None:
            return _failure("Task not found in this workspace.")
        return ToolResult(
            content=f"Updated {_describe(card)[2:]}",
            metadata={"task_ids": [card.id]},
        )

    def _set_status(self, store, workspace_id: str, task_id: str) -> ToolResult:
        from deeptutor.services.task_board import UpdateCard

        board = store.update(task_id, UpdateCard(status="done"), workspace_id=workspace_id)
        card = next((row for row in board.cards if row.id == task_id), None)
        if card is None:
            return _failure("Task not found in this workspace.")
        return ToolResult(
            content=f"Marked done: {json.dumps(card.title, ensure_ascii=False)}",
            metadata={"task_ids": [card.id]},
        )

    def _reschedule(
        self, store, workspace_id: str, task_id: str, kwargs: dict[str, Any]
    ) -> ToolResult:
        from deeptutor.services.task_board import UpdateCard

        due = _due_date(kwargs.get("due_date"))
        if not due:
            return _failure("reschedule needs a due_date (YYYY-MM-DD).")
        board = store.update(
            task_id, UpdateCard(due_date=due), workspace_id=workspace_id
        )
        card = next((row for row in board.cards if row.id == task_id), None)
        if card is None:
            return _failure("Task not found in this workspace.")
        return ToolResult(
            content=f"Rescheduled to {due}: {_describe(card)[2:]}",
            metadata={"task_ids": [card.id]},
        )


__all__ = [
    "PLANNER_TOOL_NAMES",
    "TASK_ACTIONS",
    "TASKS_TOOL_NAME",
    "TasksTool",
]
