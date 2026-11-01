"""Turning a goal into a reviewable plan, with the rules in code.

A model is good at proposing what to study and bad at arithmetic: it will
double-book Thursday, forget the review that is already due, and estimate every
task at an hour. So this module accepts *proposals* and owns everything that
must be deterministic — estimates, time-block placement, duplicate detection,
overlap marking, and the daily ceiling.

The output is ``PlanItemInput`` rows, not stored items: a generator has no
draft id yet, and the store assigns item ids in payload order, which is exactly
the order this module returns. ``depends_on`` therefore points at the id the
store *will* assign (``i1``…``iN``), which is why tasks are emitted before the
blocks that serve them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import re
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from deeptutor.services.planner.models import PlanItemInput
from deeptutor.services.planner.slots import Interval, SlotPolicy, find_free_slots

#: Product settings, not measurements. Keyed by the item kind a proposal names;
#: a finer table (per task type) needs a type on the model, which is a later
#: refinement rather than something to fake here.
ESTIMATE_MINUTES: dict[str, int] = {
    "task": 45,
    "review": 20,
}

#: One spaced-repetition card is a few minutes; the engine's due count becomes
#: a single locked row rather than a list of near-identical tiny rows.
REVIEW_MINUTES_EACH = 5

#: Reviews outrank study tasks when a day is full: skipping a due review puts it
#: straight back on tomorrow's queue.
KIND_PRIORITY: dict[str, int] = {"review": 0, "task": 1}

_WEEKDAYS = 7


@dataclass
class PlanProposal:
    """Everything a caller might want to know about one generated plan."""

    items: list[PlanItemInput] = field(default_factory=list)
    dropped_blocks: int = 0
    duplicates: int = 0
    overlaps: int = 0
    review_due: int = 0
    skipped_refused: int = 0

    @property
    def minutes(self) -> int:
        return sum(item.duration_min or 0 for item in self.items if item.kind == "block")


def normalize_title(value: str) -> str:
    """Comparison form for duplicate detection: case, width and spacing do not
    distinguish two tasks, because a learner typing the same title twice does
    not mean two of them."""
    text = str(value or "")
    text = text.replace("\u3000", " ").replace("\u00a0", " ")
    # Full-width ASCII → half-width, so "ＡＢＣ" and "ABC" collide.
    text = "".join(
        chr(ord(char) - 0xFEE0) if "\uff01" <= char <= "\uff5e" else char for char in text
    )
    return re.sub(r"\s+", "", text).casefold()


def _zone(tzid: str) -> ZoneInfo:
    try:
        return ZoneInfo(tzid)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def _monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


def week_window(week_start: date, tzid: str) -> tuple[datetime, datetime]:
    """The planning week as instants, in the reader's zone."""
    zone = _zone(tzid)
    start = _monday(week_start)
    return (
        datetime.combine(start, time(0), tzinfo=zone),
        datetime.combine(start + timedelta(days=_WEEKDAYS), time(0), tzinfo=zone),
    )


def busy_from_occurrences(occurrences: Iterable[Any], tzid: str) -> list[Interval]:
    """Calendar rows as busy spans. All-day rows own their days, nothing more."""
    from deeptutor.services.calendar.recurrence import parse_instant

    zone = _zone(tzid)
    spans: list[Interval] = []
    for occurrence in occurrences or []:
        if getattr(occurrence, "all_day", False):
            first = date.fromisoformat(occurrence.start)
            last = date.fromisoformat(occurrence.end)  # exclusive end
            spans.append(
                Interval(
                    datetime.combine(first, time(0), tzinfo=zone),
                    datetime.combine(last, time(0), tzinfo=zone),
                )
            )
        else:
            spans.append(Interval(parse_instant(occurrence.start), parse_instant(occurrence.end)))
    return spans


@dataclass
class _Task:
    """A task proposal plus the block, if any, that was allocated for it."""

    title: str
    due: date
    kind: str
    estimate: int
    start: datetime | None = None
    block_minutes: int = 0
    conflict: str = "none"


def _conflict_for(
    *, title: str, due: date, existing_titles: dict[str, set[str]], explicit: datetime | None, busy: list[Interval]
) -> str:
    if normalize_title(title) in existing_titles.get(due.isoformat(), set()):
        return "duplicate"
    if explicit is not None:
        for span in busy:
            if explicit < span.end and explicit + timedelta(minutes=1) > span.start:
                return "overlap"
    return "none"


def _slot_for(
    slots: list[Interval],
    due: date,
    wanted: int,
    zone: ZoneInfo,
    *,
    by_day: dict[str, int],
    ceiling: int,
    granularity: int,
) -> tuple[int, int] | None:
    """``(slot index, minutes)`` for a task due on ``due``, or ``None``.

    The due day wins; failing that, the latest earlier day (closest to the
    deadline). Nothing after the due date is ever used — a block that lands
    after the work was due is worse than no block — and every candidate is
    clipped to whatever that day's ceiling has left.
    """
    best_earlier: tuple[int, int] | None = None
    for index, slot in enumerate(slots):
        day = slot.start.astimezone(zone).date()
        if day > due:
            break  # slots arrive in time order
        remaining = ceiling - by_day.get(day.isoformat(), 0)
        allowed = (min(wanted, remaining) // granularity) * granularity
        if allowed < granularity or slot.minutes < allowed:
            continue
        if day == due:
            return index, allowed
        best_earlier = (index, allowed)
    return best_earlier


def _allocate(
    tasks: list[_Task],
    busy: list[Interval],
    window: tuple[datetime, datetime],
    policy: SlotPolicy,
    tzid: str,
) -> int:
    """Places one block per task inside free time, keeping the day under budget.

    Returns the number of tasks left without a block. A task without a block is
    still a real task: the ceiling limits what lands on the calendar, not what
    the learner has to do.
    """
    zone = _zone(tzid)
    slots = find_free_slots(busy, window, policy, tzid=tzid)
    granularity = max(1, int(policy.granularity_min))
    ceiling = int(policy.daily_max_min)
    by_day: dict[str, int] = {}
    dropped = 0
    for task in sorted(tasks, key=lambda item: (item.due, item.title)):
        wanted = min(int(task.estimate), ceiling)
        if task.start is not None:
            # An explicit start is honoured as given: the model may have a
            # reason to place it there, and overlap is reported, not moved.
            minutes = max(granularity, (wanted // granularity) * granularity)
            task.block_minutes = minutes
            key = task.start.astimezone(zone).date().isoformat()
            by_day[key] = by_day.get(key, 0) + minutes
            continue
        chosen = _slot_for(
            slots,
            task.due,
            wanted,
            zone,
            by_day=by_day,
            ceiling=ceiling,
            granularity=granularity,
        )
        if chosen is None:
            dropped += 1
            continue
        index, minutes = chosen
        slot = slots[index]
        end = slot.start + timedelta(minutes=minutes)
        rest = Interval(end, slot.end)
        slots[index : index + 1] = [rest] if rest.minutes >= granularity else []
        task.start = slot.start
        task.block_minutes = minutes
        key = slot.start.astimezone(zone).date().isoformat()
        by_day[key] = by_day.get(key, 0) + minutes
    return dropped


def build_plan(
    *,
    goal: str,
    week_start: date,
    workspace_id: str,
    tzid: str,
    candidates: Sequence[PlanItemInput] | None = None,
    existing_tasks: Sequence[Any] = (),
    busy: Sequence[Interval] = (),
    policy: SlotPolicy | None = None,
    review_due: int = 0,
    dismissed_recent: Sequence[str] = (),
) -> PlanProposal:
    """Builds the reviewable rows for one week.

    ``workspace_id`` is accepted (and unused for reads) so every caller passes
    it explicitly: a plan is scoped to a workspace, and threading it later would
    quietly allow a plan built from one board to be applied to another.
    """
    policy = policy or SlotPolicy()
    zone = _zone(tzid)
    start_of_week = _monday(week_start)
    window = week_window(start_of_week, tzid)
    spans = list(busy)

    existing_titles: dict[str, set[str]] = {}
    for card in existing_tasks or ():
        if getattr(card, "archived", False):
            continue
        due = getattr(card, "due_date", None)
        if not due:
            continue
        existing_titles.setdefault(str(due), set()).add(normalize_title(card.title))

    tasks: list[_Task] = []
    draft_titles: set[tuple[str, str]] = set()
    refused = {normalize_title(title) for title in dismissed_recent if str(title).strip()}
    skipped_refused = 0
    repeated = 0
    for candidate in candidates or ():
        kind = str(getattr(candidate, "kind", "task") or "task")
        if kind == "block":
            # Blocks are the planner's to place; a proposal cannot place its own.
            continue
        due_raw = getattr(candidate, "due_date", None)
        try:
            due = date.fromisoformat(str(due_raw)) if due_raw else start_of_week
        except ValueError:
            due = start_of_week
        if due < start_of_week or due >= start_of_week + timedelta(days=_WEEKDAYS):
            due = start_of_week  # the week being planned owns the plan
        estimate = ESTIMATE_MINUTES.get(kind, ESTIMATE_MINUTES["task"])
        explicit = None
        if getattr(candidate, "start_at", None):
            from deeptutor.services.calendar.recurrence import parse_instant

            try:
                explicit = parse_instant(candidate.start_at)
            except ValueError:
                explicit = None
        normalized = normalize_title(candidate.title)
        if normalized in refused:
            # Offering a row the learner just refused reads as not listening.
            skipped_refused += 1
            continue
        key = (due.isoformat(), normalized)
        if key in draft_titles:
            # The same title twice in one draft has nothing to merge with; it
            # is noise, not a second task.
            repeated += 1
            continue
        draft_titles.add(key)
        conflict = _conflict_for(
            title=candidate.title,
            due=due,
            existing_titles=existing_titles,
            explicit=explicit,
            busy=spans,
        )
        tasks.append(
            _Task(
                title=candidate.title,
                due=due,
                kind=kind,
                estimate=estimate,
                start=explicit.astimezone(zone) if explicit else None,
                conflict=conflict,
            )
        )

    dropped = _allocate(tasks, spans, window, policy, tzid)

    # Rows are built in payload order so every ``depends_on`` can name the id
    # the store will assign for that position: reviews, then tasks, then the
    # blocks that serve them.
    rows: list[dict[str, Any]] = []
    review_minutes = max(0, int(review_due)) * REVIEW_MINUTES_EACH
    if review_minutes > 0:
        rows.append(
            {
                "kind": "review",
                "title": f"到期复习 {int(review_due)} 个",
                "due_date": start_of_week.isoformat(),
                "duration_min": min(
                    review_minutes, max(REVIEW_MINUTES_EACH, policy.daily_max_min)
                ),
                "source": "srs",
                "locked": True,
            }
        )

    ordered_tasks = sorted(tasks, key=lambda item: (item.due, item.title))
    positions = {id(task): len(rows) + index + 1 for index, task in enumerate(ordered_tasks)}
    for task in ordered_tasks:
        rows.append(
            {
                "kind": "task",
                "title": task.title,
                "due_date": task.due.isoformat(),
                # The card carries what the task needs; the block carries what
                # was actually scheduled for it.
                "duration_min": task.estimate,
                "conflict": task.conflict,
            }
        )
    for task in sorted(
        (task for task in ordered_tasks if task.start is not None),
        key=lambda item: (item.start, item.title),
    ):
        rows.append(
            {
                "kind": "block",
                "title": task.title,
                "due_date": task.due.isoformat(),
                "start_at": task.start.isoformat() if task.start else None,
                "duration_min": task.block_minutes,
                "depends_on": f"i{positions[id(task)]}",
            }
        )

    items = [PlanItemInput(**row) for row in rows]

    return PlanProposal(
        items=items,
        dropped_blocks=dropped,
        duplicates=sum(1 for task in tasks if task.conflict == "duplicate") + repeated,
        skipped_refused=skipped_refused,
        overlaps=sum(1 for task in tasks if task.conflict == "overlap"),
        review_due=int(review_due),
    )


__all__ = [
    "ESTIMATE_MINUTES",
    "KIND_PRIORITY",
    "REVIEW_MINUTES_EACH",
    "PlanProposal",
    "build_plan",
    "busy_from_occurrences",
    "normalize_title",
    "week_window",
]
