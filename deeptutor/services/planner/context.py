"""A read-only snapshot of the learner's tasks and calendar, as prompt text.

The planner tools can only be used well if the model already knows what today
holds. This module answers that without a tool call: one compact markdown
block carrying today's date, the next few days of events, open tasks, the
tasks linked to this conversation, and how much spaced-repetition work is due.

Three properties matter more than the content:

* **It never raises.** A prompt nicety must not take a whole turn down, so
  every source is isolated and a broken one degrades to a named entry in
  :attr:`PlannerSnapshot.degraded`.
* **It is bounded.** The block competes with the rest of the system prompt, so
  it is capped and degrades by dropping the least important rows first.
* **It is read-only.** Nothing here writes, and nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

#: Hard ceiling for the rendered block. The prompt is shared with personas,
#: skills, tools and memory; a plan context that grows without a limit pushes
#: the model's actual instructions out of the window.
MAX_CONTEXT_CHARS = 1200

#: Long titles are the main reason a row blows the budget: a plan names
#: chapters, not paragraphs.
MAX_TITLE_CHARS = 24

#: How many rows of each kind get rendered before the cap trims them anyway;
#: a wall of forty tasks does not help anyone decide what to do today.
MAX_EVENT_ROWS = 8
MAX_TASK_ROWS = 8
MAX_LINKED_ROWS = 6

_EMPTY = "暂无"


@dataclass(frozen=True)
class PlannerSnapshot:
    """Everything the planner prompt block is rendered from."""

    today: str
    tzid: str
    events: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    linked_tasks: list[dict[str, Any]] = field(default_factory=list)
    review_due: int = 0
    degraded: list[str] = field(default_factory=list)
    days: int = 7


@dataclass(frozen=True)
class PlannerSources:
    """The four reads the snapshot needs, injectable so tests stay offline.

    Each callable owns its own formatting: the calendar knows how to render an
    all-day range, the task board knows a due date is a plain calendar day.
    """

    events: Callable[[datetime, datetime, str], list[dict[str, Any]]]
    tasks: Callable[[str], list[dict[str, Any]]]
    linked_tasks: Callable[[str, str | None], list[dict[str, Any]]]
    review_due: Callable[[datetime, datetime], int]


def _shorten(value: str) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= MAX_TITLE_CHARS:
        return text
    return text[: MAX_TITLE_CHARS - 1].rstrip() + "…"


def _event_row(row: dict[str, Any]) -> str:
    at = str(row.get("at") or "").strip()
    title = _shorten(row.get("title") or "")
    return f"{at} {title}".strip()


def _task_row(row: dict[str, Any]) -> str:
    title = _shorten(row.get("title") or "")
    due = str(row.get("due") or "").strip()
    return f"{title}（{due} 到期）" if due else f"{title}（无日期）"


def _join(rows: list[str]) -> str:
    return "；".join(row for row in rows if row)


def _render(
    snapshot: PlannerSnapshot,
    events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    linked: list[dict[str, Any]],
    *,
    truncated: bool,
) -> str:
    lines = ["## 规划上下文", f"今天是 {snapshot.today}（{snapshot.tzid}）。"]

    if events:
        lines.append(
            f"- 未来 {snapshot.days} 天已排事件 {len(events)} 个：{_join([_event_row(r) for r in events])}"
        )
    else:
        lines.append(f"- 未来 {snapshot.days} 天已排事件：{_EMPTY}")

    if tasks:
        lines.append(
            f"- 未来 {snapshot.days} 天到期任务 {len(tasks)} 个：{_join([_task_row(r) for r in tasks])}"
        )
    else:
        lines.append(f"- 未来 {snapshot.days} 天到期任务：{_EMPTY}")

    if linked:
        lines.append(f"- 本会话关联任务：{_join([_shorten(r.get('title') or '') for r in linked])}")
    else:
        lines.append(f"- 本会话关联任务：{_EMPTY}")

    lines.append(f"- 间隔重复到期 {snapshot.review_due} 个")

    if snapshot.degraded:
        lines.append(f"（部分数据读取失败：{'，'.join(snapshot.degraded)}）")
    if truncated:
        lines.append("（已截断）")
    return "\n".join(lines)


def render_planner_context(snapshot: PlannerSnapshot) -> str:
    """Renders the block, trimming rows until it fits :data:`MAX_CONTEXT_CHARS`."""
    events = list(snapshot.events[:MAX_EVENT_ROWS])
    tasks = list(snapshot.tasks[:MAX_TASK_ROWS])
    linked = list(snapshot.linked_tasks[:MAX_LINKED_ROWS])
    truncated = (
        len(snapshot.events) > len(events)
        or len(snapshot.tasks) > len(tasks)
        or len(snapshot.linked_tasks) > len(linked)
    )
    while True:
        text = _render(snapshot, events, tasks, linked, truncated=truncated)
        if len(text) <= MAX_CONTEXT_CHARS:
            return text
        # Events go first: a task without its time block is still actionable,
        # while a time block whose task was dropped reads as a mystery meeting.
        if events:
            events = events[:-1]
            truncated = True
        elif tasks:
            tasks = tasks[:-1]
            truncated = True
        elif linked:
            linked = linked[:-1]
            truncated = True
        else:
            return text[:MAX_CONTEXT_CHARS]


def local_zone_name() -> str:
    """The host's own zone, as well as it can be determined.

    Nothing in the turn payload names the reader's zone yet, and a planner that
    says "today" in the wrong zone is worse than no planner at all. This is the
    best available answer for a local-first install; callers that know the
    reader's zone must pass it explicitly instead.
    """
    env = os.environ.get("TZ", "").strip()
    if env and "/" in env:
        return env
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if "/" in text:
            return text
    except OSError:
        pass
    try:
        parts = Path("/etc/localtime").resolve().parts
        if "zoneinfo" in parts:
            return "/".join(parts[parts.index("zoneinfo") + 1 :])
    except OSError:
        pass
    return "UTC"


def _zone(tzid: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001 - an unknown zone must not cost the block
        return ZoneInfo("UTC")


def _today_label(moment: datetime, tzid: str) -> str:
    local = moment.astimezone(_zone(tzid))
    return f"{local.date().isoformat()} {local.strftime('%a')}"


def _default_sources(
    *, workspace_id: str, session_id: str | None, tzid: str, moment: datetime, days: int
) -> PlannerSources:
    """The real reads, imported lazily so the module stays testable offline."""

    def _events(lo: datetime, hi: datetime, tz_name: str) -> list[dict[str, Any]]:
        from deeptutor.services.calendar.store import get_calendar_store

        payload = get_calendar_store().occurrences(lo, hi, tz_name)
        zone = _zone(tz_name)
        rows: list[dict[str, Any]] = []
        for occurrence in payload.get("occurrences") or []:
            if occurrence.all_day:
                # An all-day row is a local calendar day, not an instant; it
                # must not be shifted by the zone conversion below.
                try:
                    when = datetime.fromisoformat(occurrence.start).strftime("%m/%d") + " 全天"
                except ValueError:
                    when = occurrence.start
            else:
                try:
                    instants = datetime.fromisoformat(occurrence.start.replace("Z", "+00:00"))
                except ValueError:
                    continue
                when = instants.astimezone(zone).strftime("%m/%d %H:%M")
            rows.append({"at": when, "title": occurrence.title, "task_id": occurrence.task_id})
        return rows

    def _board_scope():
        from deeptutor.services.task_board import get_task_board_store, workspace_board

        return workspace_board(get_task_board_store().read(), workspace_id)

    def _tasks(_workspace_id: str) -> list[dict[str, Any]]:
        # The whole board is already in the prompt as ``[Task context]``; what
        # it cannot say is which cards fall inside the window being planned.
        # This is that slice — not a second copy of the board.
        first = moment.date().isoformat()
        last = (moment + timedelta(days=max(1, int(days)))).date().isoformat()
        rows = []
        for card in _board_scope().cards:
            if card.archived or card.status == "done" or not card.due_date:
                continue
            if not first <= card.due_date <= last:
                continue
            rows.append({"title": card.title, "due": card.due_date, "id": card.id})
        rows.sort(key=lambda row: (row["due"] or "", row["title"]))
        return rows

    def _linked(_workspace_id: str, wanted_session: str | None) -> list[dict[str, Any]]:
        if not wanted_session:
            return []
        board = _board_scope()
        by_id = {card.id: card for card in board.cards}
        linked_ids: list[str] = []
        for link in board.session_links:
            if link.session_id == wanted_session:
                linked_ids.extend(link.task_ids)
        rows = []
        for task_id in linked_ids:
            card = by_id.get(task_id)
            if card is None or card.archived:
                continue
            rows.append({"title": card.title, "due": card.due_date, "id": card.id})
        return rows

    def _review_due(_lo: datetime, hi: datetime) -> int:
        from deeptutor.modules.catalog import module_enabled

        if not module_enabled("mastery"):
            return 0
        from deeptutor.learning.scheduler import SpacedRepetitionScheduler
        from deeptutor.learning.storage import LearningStore

        scheduler = SpacedRepetitionScheduler()
        learning = LearningStore()
        due = 0
        for progress, _topic, _count, _interaction in learning.list_topic_snapshots():
            for task in scheduler.build_review_queue(progress):
                if task.due_at < hi.timestamp():
                    due += 1
        return due

    return PlannerSources(
        events=_events, tasks=_tasks, linked_tasks=_linked, review_due=_review_due
    )


def turn_zone(config: Mapping[str, Any] | None) -> str | None:
    """The reader's zone, when the turn named a usable one.

    ``TurnRequest.config`` is free-form, so the browser can report the zone
    without a wire-protocol change. An unusable value is ignored rather than
    repaired: the host-zone fallback is already the best guess we have, and a
    bad zone must never cost the block.
    """
    if not isinstance(config, Mapping):
        return None
    raw = str(config.get("timezone") or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
    except Exception:  # noqa: BLE001 - unknown zone, fall back to the host
        return None
    return raw


def load_planner_context(
    workspace_id: str,
    *,
    days: int = 7,
    tzid: str | None = None,
    sources: PlannerSources | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Builds the planner prompt block for one workspace and conversation."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    zone_name = str(tzid or local_zone_name() or "UTC")
    horizon = max(1, int(days))
    hi = moment + timedelta(days=horizon)
    active = sources or _default_sources(
        workspace_id=workspace_id,
        session_id=session_id,
        tzid=zone_name,
        moment=moment,
        days=horizon,
    )

    events: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    linked: list[dict[str, Any]] = []
    review_due = 0
    degraded: list[str] = []

    # Each read stands alone: one broken source must not cost the others, and
    # a prompt block is never worth failing a turn over.
    try:
        events = list(active.events(moment, hi, zone_name))
    except Exception:  # noqa: BLE001 - degrade, never raise, for a prompt block
        degraded.append("events")
    try:
        tasks = list(active.tasks(workspace_id))
    except Exception:  # noqa: BLE001
        degraded.append("tasks")
    try:
        linked = list(active.linked_tasks(workspace_id, session_id))
    except Exception:  # noqa: BLE001
        degraded.append("linked_tasks")
    try:
        review_due = int(active.review_due(moment, hi))
    except Exception:  # noqa: BLE001
        degraded.append("review_due")

    snapshot = PlannerSnapshot(
        today=_today_label(moment, zone_name),
        tzid=zone_name,
        events=events,
        tasks=tasks,
        linked_tasks=linked,
        review_due=review_due,
        degraded=degraded,
        days=horizon,
    )
    return render_planner_context(snapshot)


def build_turn_planner_context(
    *,
    config: Mapping[str, Any] | None,
    workspace_id: str,
    session_id: str | None = None,
) -> str:
    """The block a chat turn injects: the reader's zone wins over the host's."""
    return load_planner_context(
        workspace_id, session_id=session_id, tzid=turn_zone(config)
    )


__all__ = [
    "MAX_CONTEXT_CHARS",
    "build_turn_planner_context",
    "turn_zone",
    "PlannerSnapshot",
    "local_zone_name",
    "PlannerSources",
    "load_planner_context",
    "render_planner_context",
]
