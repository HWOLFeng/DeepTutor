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
CALENDAR_TOOL_NAME = "calendar"
PLANNER_TOOL_NAMES: tuple[str, ...] = (TASKS_TOOL_NAME, CALENDAR_TOOL_NAME)

TASK_ACTIONS = ("list", "create", "update", "complete", "reschedule")
CALENDAR_ACTIONS = ("list_events", "create_event", "update_event", "delete_event", "find_free_slots")


class _PromptHintsMixin:
    def get_prompt_hints(self, language: str = "en"):
        return load_prompt_hints(self.name, language=language)


def _board_store():
    """Indirection so tests can stand in for the account store."""
    from deeptutor.services.task_board import get_task_board_store

    return get_task_board_store()


def _calendar_store():
    """Indirection so tests can stand in for the account calendar."""
    from deeptutor.services.calendar.store import get_calendar_store

    return get_calendar_store()


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


def _failure(message: str, **metadata: Any) -> ToolResult:
    return ToolResult(content=message, success=False, metadata=metadata or {"task_ids": []})


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


def _instant(raw: Any, field: str):
    """Parses an ISO instant the way the calendar store will."""
    from deeptutor.services.calendar.recurrence import parse_instant

    text = str(raw or "").strip()
    if not text:
        raise ValueError(f"{field} is required (ISO 8601, e.g. 2026-11-02T19:00).")
    return parse_instant(text)


def _zone_name(raw: Any) -> str:
    from deeptutor.services.calendar.recurrence import zone

    from deeptutor.services.planner.context import local_zone_name

    name = str(raw or "").strip() or local_zone_name()
    zone(name)  # validates; an unknown zone must fail loudly, not shift silently
    return name


def _busy(occurrences: list[Any], tzid: str):
    """Calendar rows as busy spans, with all-day rows kept to their own days."""
    from datetime import datetime, time
    from datetime import date as date_type

    from deeptutor.services.calendar.recurrence import parse_instant, zone
    from deeptutor.services.planner.slots import Interval

    local = zone(tzid)
    spans = []
    for occurrence in occurrences:
        if occurrence.all_day:
            first = date_type.fromisoformat(occurrence.start)
            last = date_type.fromisoformat(occurrence.end)  # exclusive end
            spans.append(
                Interval(
                    datetime.combine(first, time(0), tzinfo=local),
                    datetime.combine(last, time(0), tzinfo=local),
                )
            )
        else:
            spans.append(Interval(parse_instant(occurrence.start), parse_instant(occurrence.end)))
    return spans


def _slot_label(item, tzid: str) -> str:
    from deeptutor.services.calendar.recurrence import zone

    local = zone(tzid)
    return f"{item.start.astimezone(local):%m/%d %H:%M}–{item.end.astimezone(local):%H:%M}"


class CalendarTool(_PromptHintsMixin, BaseTool):
    """Read the calendar, place one time block, and answer "when am I free"."""

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=CALENDAR_TOOL_NAME,
            description=(
                "Read and edit the learner's calendar in the active workspace. "
                "`list_events` for a window, `create_event` for one event (or one "
                "time block for a task, via task_id), `update_event`, "
                "`delete_event`, and `find_free_slots` for the free windows you "
                "can put study blocks in. One event per call. Timed events take "
                "ISO 8601 instants; all-day events take YYYY-MM-DD with an "
                "exclusive end. All-day rows occupy only their own days."
            ),
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="What to do with the calendar.",
                    enum=list(CALENDAR_ACTIONS),
                ),
                ToolParameter(
                    name="start",
                    type="string",
                    description="Window start (ISO 8601) or the event's start.",
                    required=False,
                ),
                ToolParameter(
                    name="end",
                    type="string",
                    description="Window end (ISO 8601) or the event's end.",
                    required=False,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Event title (create: required).",
                    required=False,
                ),
                ToolParameter(
                    name="event_id",
                    type="string",
                    description="Target event id (update/delete).",
                    required=False,
                ),
                ToolParameter(
                    name="all_day",
                    type="boolean",
                    description="True for a date-only event; start/end are then dates.",
                    required=False,
                ),
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="Make this event a time block for that task card.",
                    required=False,
                ),
                ToolParameter(
                    name="tzid",
                    type="string",
                    description="Zone for the times (defaults to the reader's zone).",
                    required=False,
                ),
                ToolParameter(
                    name="notes",
                    type="string",
                    description="Free-form note on the event.",
                    required=False,
                ),
            ],
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        action = str(kwargs.get("action") or "").strip().lower()
        if action not in CALENDAR_ACTIONS:
            return _failure(
                f"Unknown action {action!r}. Use one of: {', '.join(CALENDAR_ACTIONS)}."
            )
        try:
            workspace_id = _workspace()
            store = _calendar_store()
            if action == "list_events":
                return self._list(store, kwargs)
            if action == "create_event":
                return self._create(store, workspace_id, kwargs)
            if action == "find_free_slots":
                return self._free(store, kwargs)
            event_id = str(kwargs.get("event_id") or "").strip()
            if not event_id:
                return _failure(f"{action} needs an event_id (see action='list_events').")
            if action == "update_event":
                return self._update(store, workspace_id, event_id, kwargs)
            return self._delete(store, event_id)
        except KeyError:
            return _failure("Event not found.")
        except ValueError as exc:
            return _failure(str(exc))

    def _list(self, store, kwargs: dict[str, Any]) -> ToolResult:
        tzid = _zone_name(kwargs.get("tzid"))
        lo, hi = _instant(kwargs.get("start"), "start"), _instant(kwargs.get("end"), "end")
        payload = store.occurrences(lo, hi, tzid)
        rows = payload.get("occurrences") or []
        if not rows:
            return ToolResult(content="No events in that window.", metadata={"event_ids": []})
        lines = [f"{len(rows)} event(s) in that window:"]
        for row in rows:
            when = (
                f"{row.start} (all day)"
                if row.all_day
                else f"{row.start} → {row.end}"
            )
            suffix = f" · task `{row.task_id}`" if row.task_id else ""
            lines.append(f"- `{row.id}` {when} · {row.title}{suffix}")
        return ToolResult(
            content="\n".join(lines),
            metadata={"event_ids": [row.id for row in rows]},
        )

    def _create(self, store, workspace_id: str, kwargs: dict[str, Any]) -> ToolResult:
        from deeptutor.services.calendar.models import EventInput
        from deeptutor.services.planner.links import require_owned_task

        title = str(kwargs.get("title") or "").strip()
        if not title:
            return _failure("create_event needs a title.")
        tzid = _zone_name(kwargs.get("tzid"))
        all_day = bool(kwargs.get("all_day"))
        task_id = str(kwargs.get("task_id") or "").strip() or None
        require_owned_task(task_id, workspace_id)
        payload = EventInput(
            title=title,
            notes=str(kwargs.get("notes") or ""),
            all_day=all_day,
            start=str(kwargs.get("start") or "").strip(),
            end=str(kwargs.get("end") or "").strip(),
            tzid=tzid,
            task_id=task_id,
            task_workspace_id=workspace_id if task_id else None,
        )
        result = store.create(payload)
        event = result["event"]
        return ToolResult(
            content=f"Created `{event.id}` {event.start} → {event.end} · {event.title}",
            metadata={"event_ids": [event.id]},
        )

    def _update(
        self, store, workspace_id: str, event_id: str, kwargs: dict[str, Any]
    ) -> ToolResult:
        from deeptutor.services.calendar.models import EventPatch, EventPatchRequest
        from deeptutor.services.planner.links import require_owned_task

        changes: dict[str, Any] = {}
        for field in ("title", "notes", "start", "end"):
            if kwargs.get(field) is not None and str(kwargs.get(field)).strip():
                changes[field] = str(kwargs[field]).strip()
        if kwargs.get("all_day") is not None:
            changes["all_day"] = bool(kwargs["all_day"])
        if kwargs.get("tzid"):
            changes["tzid"] = _zone_name(kwargs["tzid"])
        if "start" in changes or "end" in changes:
            if not {"start", "end"} <= set(changes):
                return _failure("update_event needs both start and end when moving an event.")
        task_id = kwargs.get("task_id")
        if task_id is not None:
            cleaned = str(task_id).strip() or None
            require_owned_task(cleaned, workspace_id)
            changes["task_id"] = cleaned
            changes["task_workspace_id"] = workspace_id if cleaned else None
        if not changes:
            return _failure("update_event needs at least one field to change.")
        request = EventPatchRequest(changes=EventPatch(**changes))
        store.update(event_id, request)
        return ToolResult(
            content=f"Updated `{event_id}`.",
            metadata={"event_ids": [event_id]},
        )

    def _delete(self, store, event_id: str) -> ToolResult:
        store.delete(event_id, scope="all", occurrence=None)
        return ToolResult(
            content=f"Deleted `{event_id}`.",
            metadata={"event_ids": [event_id]},
        )

    def _free(self, store, kwargs: dict[str, Any]) -> ToolResult:
        from deeptutor.services.planner.slots import SlotPolicy, find_free_slots

        tzid = _zone_name(kwargs.get("tzid"))
        lo, hi = _instant(kwargs.get("start"), "start"), _instant(kwargs.get("end"), "end")
        if hi <= lo:
            return _failure("find_free_slots needs end after start.")
        payload = store.occurrences(lo, hi, tzid)
        spans = _busy(payload.get("occurrences") or [], tzid)
        slots = find_free_slots(spans, (lo, hi), SlotPolicy(), tzid=tzid)
        if not slots:
            return ToolResult(content="No free study time in that window.", metadata={"slots": []})
        lines = [f"{len(slots)} free window(s):"]
        lines.extend(f"- {_slot_label(item, tzid)} ({item.minutes} min)" for item in slots)
        return ToolResult(
            content="\n".join(lines),
            metadata={
                "slots": [[item.start.isoformat(), item.end.isoformat()] for item in slots]
            },
        )


__all__ = [
    "CALENDAR_ACTIONS",
    "CALENDAR_TOOL_NAME",
    "CalendarTool",
    "PLANNER_TOOL_NAMES",
    "TASK_ACTIONS",
    "TASKS_TOOL_NAME",
    "TasksTool",
]
