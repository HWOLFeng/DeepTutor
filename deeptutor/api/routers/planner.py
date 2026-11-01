"""Accepting a plan draft: what the learner picked, written once.

This router is the **only** way a draft becomes cards and events. It is
deliberately not a tool: the model can propose a plan, explain it, and revise
it, but the decision about which rows are adopted belongs to the person reading
the card. Keeping the endpoint out of the tool surface is what makes that true
at the protocol level rather than by convention.

Three invariants:

* **Nothing is written until it is asked for.** Unmentioned items stay pending.
* **An accepted block always belongs to an accepted task.** Otherwise the
  calendar would carry time set aside for work that does not exist.
* **Applying twice writes once.** A second call replays the first result.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from deeptutor.services.calendar.models import EventInput
from deeptutor.services.calendar.store import get_calendar_store
from deeptutor.services.planner.models import PlanDraft
from deeptutor.services.planner.planner import normalize_title
from deeptutor.services.planner.store import get_plan_draft_store
from deeptutor.services.task_board import CreateCard, get_task_board_store, workspace_board

from .task_board import board_workspace

router = APIRouter()

Decision = Literal["accepted", "dismissed"]


class ApplyRequest(BaseModel):
    """Which rows the learner kept. Anything unmentioned is not written."""

    model_config = ConfigDict(extra="forbid")

    decisions: dict[str, Decision] = Field(default_factory=dict)
    #: Optional tags explaining a dismissal. Absent means "no reason given",
    #: which is a legitimate answer — never a validation error.
    reasons: dict[str, str] | None = None


class ApplyOutcome(BaseModel):
    applied: int
    merged: int
    skipped: int
    task_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    undo_token: str
    revision: dict[str, int] = Field(default_factory=dict)


class UndoOutcome(BaseModel):
    removed: int
    skipped: list[str] = Field(default_factory=list)
    already_undone: bool = False
    revision: dict[str, int] = Field(default_factory=dict)


class ItemPatchRequest(BaseModel):
    """Inline edits made before accepting; absent fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    due_date: str | None = Field(default=None, max_length=10)
    start_at: str | None = Field(default=None, max_length=40)
    duration_min: int | None = Field(default=None, ge=1, le=600)


@contextmanager
def errors():
    try:
        yield
    except KeyError as exc:
        raise HTTPException(404, "Plan draft not found.") from exc
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _draft_for(draft_id: str, workspace_id: str) -> PlanDraft:
    draft = get_plan_draft_store().read(draft_id)
    if draft.workspace_id != workspace_id:
        # A draft from another workspace reads exactly like a missing one.
        raise KeyError(draft_id)
    return draft


def _norm(value: str | None) -> str:
    return normalize_title(value or "")


@router.get("/drafts/{draft_id}", response_model=PlanDraft)
def read_draft(draft_id: str, request: Request) -> PlanDraft:
    workspace_id = board_workspace(request)
    with errors():
        return _draft_for(draft_id, workspace_id)


@router.patch("/drafts/{draft_id}/items/{item_id}", response_model=PlanDraft)
def patch_item(
    draft_id: str, item_id: str, payload: ItemPatchRequest, request: Request
) -> PlanDraft:
    workspace_id = board_workspace(request)
    with errors():
        draft = _draft_for(draft_id, workspace_id)
        changes = {
            field: getattr(payload, field)
            for field in payload.model_fields_set
            if field in {"title", "due_date", "start_at", "duration_min"}
        }
        if not changes:
            return draft
        return get_plan_draft_store().patch_item(draft_id, item_id, changes)


@router.post("/drafts/{draft_id}/apply", response_model=ApplyOutcome)
def apply_draft(draft_id: str, payload: ApplyRequest, request: Request) -> ApplyOutcome:
    workspace_id = board_workspace(request)
    store = get_plan_draft_store()
    with errors():
        draft = _draft_for(draft_id, workspace_id)
    if draft.status == "applied" and draft.apply_result:
        # Replay, not a second write: a double-tapped confirm must not double
        # the board.
        return ApplyOutcome(**draft.apply_result)
    if draft.status in {"undone", "expired"}:
        raise HTTPException(409, f"This draft is {draft.status}.")
    if draft.status not in {"drafting", "reviewing"}:
        raise HTTPException(409, f"This draft is {draft.status}.")

    with errors():
        if payload.decisions:
            draft = store.set_decisions(
                draft_id, dict(payload.decisions), payload.reasons
            )

    accepted = {item.item_id: item for item in draft.items if item.decision == "accepted"}
    orphaned = [
        item.item_id
        for item in accepted.values()
        if item.kind == "block" and item.depends_on not in accepted
    ]
    if orphaned:
        raise HTTPException(
            422,
            "An accepted time block needs its task accepted too: "
            + ", ".join(sorted(orphaned)),
        )

    board_store = get_task_board_store()
    calendar_store = get_calendar_store()
    cards_before = len(workspace_board(board_store.read(), workspace_id).cards)
    events_before = len(
        calendar_store.occurrences(
            _far_past(), _far_future(), draft.tzid
        ).get("occurrences")
        or []
    )

    existing = {
        (_norm(card.title), card.due_date): card
        for card in workspace_board(board_store.read(), workspace_id).cards
        if not card.archived and card.due_date
    }
    created_cards: dict[str, str] = {}  # draft item_id -> card id
    applied_rows: list[dict[str, Any]] = []
    task_ids: list[str] = []
    merged = 0
    for item in draft.items:
        if item.decision != "accepted" or item.kind == "block":
            continue
        # The engine-generated review row becomes a card too: "12 reviews due"
        # is work the learner should see on the board, not just in a draft.
        landed = existing.get((_norm(item.title), item.due_date))
        if landed is not None:
            # Merged rows are the learner's own card: recorded, never deleted
            # by an undo that only promised to take back what it created.
            created_cards[item.item_id] = landed.id
            merged += 1
            continue
        board = board_store.create(
            CreateCard(title=item.title, due_date=item.due_date), workspace_id
        )
        card = max(
            (row for row in workspace_board(board, workspace_id).cards if not row.archived),
            key=lambda row: row.created_at,
        )
        created_cards[item.item_id] = card.id
        task_ids.append(card.id)
        applied_rows.append(
            {
                "kind": item.kind,
                "title": card.title,
                "due_date": card.due_date,
                "card_id": card.id,
            }
        )

    event_ids: list[str] = []
    for item in draft.items:
        if item.decision != "accepted" or item.kind != "block":
            continue
        if not item.start_at or not item.duration_min:
            continue
        start = _instant(item.start_at)
        end = start + timedelta(minutes=int(item.duration_min))
        event = calendar_store.create(
            EventInput(
                title=item.title,
                start=start.isoformat(),
                end=end.isoformat(),
                tzid=draft.tzid,
                task_id=created_cards.get(item.depends_on or ""),
                task_workspace_id=workspace_id if created_cards.get(item.depends_on or "") else None,
            )
        )["event"]
        event_ids.append(event.id)
        applied_rows.append(
            {
                "kind": "block",
                "title": event.title,
                "start_at": item.start_at,
                "event_id": event.id,
            }
        )

    cards_after = len(workspace_board(board_store.read(), workspace_id).cards)
    events_after = len(
        calendar_store.occurrences(
            _far_past(), _far_future(), draft.tzid
        ).get("occurrences")
        or []
    )
    undo_token = uuid4().hex
    outcome = ApplyOutcome(
        applied=len(accepted),
        merged=merged,
        skipped=sum(1 for item in draft.items if item.decision == "dismissed"),
        task_ids=task_ids,
        event_ids=event_ids,
        undo_token=undo_token,
        revision={
            "calendar": calendar_store.revision(),
            "board": board_store.revision(),
        },
    )
    store.record_undo(
        undo_token,
        draft_id,
        before={"cards": cards_before, "events": events_before},
        after={
            "cards": cards_after,
            "events": events_after,
            # What this batch wrote, row by row: undo compares against these
            # expectations instead of guessing from timestamps.
            "items": applied_rows,
        },
        task_ids=task_ids,
        event_ids=event_ids,
    )
    stored, _first = store.mark_applied(draft_id, outcome.model_dump(), undo_token)
    if stored.apply_result and stored.apply_result != outcome.model_dump():
        return ApplyOutcome(**stored.apply_result)
    return outcome


@router.post("/undo/{token}", response_model=UndoOutcome)
def undo_batch(token: str, request: Request) -> UndoOutcome:
    """Takes back one applied batch, row by row.

    A row the learner has since edited is left alone and reported by name: undo
    restores what this batch wrote, it does not overrule later decisions. Rows
    that were merged into an existing card are never touched — that card was
    never this batch's to delete.
    """
    workspace_id = board_workspace(request)
    store = get_plan_draft_store()
    with errors():
        snapshot = store.read_undo(token)
        draft = _draft_for(snapshot.draft_id, workspace_id)
    if snapshot.undone:
        return UndoOutcome(removed=0, already_undone=True)

    board_store = get_task_board_store()
    calendar_store = get_calendar_store()
    removed = 0
    skipped: list[str] = []
    for entry in snapshot.after.get("items") or []:
        if entry.get("card_id"):
            card = next(
                (
                    row
                    for row in workspace_board(board_store.read(), workspace_id).cards
                    if row.id == entry["card_id"]
                ),
                None,
            )
            if card is None:
                continue
            if card.title != entry.get("title") or card.due_date != entry.get("due_date"):
                skipped.append(card.title)
                continue
            try:
                board_store.delete(card.id, workspace_id=workspace_id)
            except KeyError:
                continue
            removed += 1
            continue
        event_id = entry.get("event_id")
        if not event_id:
            continue
        occurrence = _occurrence(calendar_store, event_id, draft.tzid)
        if occurrence is None:
            continue
        if _instant(occurrence.start) != _instant(str(entry.get("start_at"))):
            skipped.append(occurrence.title)
            continue
        try:
            calendar_store.delete(event_id)
        except KeyError:
            continue
        removed += 1

    store.mark_undone(token)
    return UndoOutcome(
        removed=removed,
        skipped=skipped,
        revision={
            "calendar": calendar_store.revision(),
            "board": board_store.revision(),
        },
    )


def _occurrence(store, event_id: str, tzid: str):
    payload = store.occurrences(_far_past(), _far_future(), tzid)
    for row in payload.get("occurrences") or []:
        if row.event_id == event_id:
            return row
    return None


def _instant(value: str):
    from deeptutor.services.calendar.recurrence import parse_instant

    return parse_instant(value)


def _far_past():
    from datetime import datetime, timezone

    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _far_future():
    from datetime import datetime, timezone

    return datetime(2100, 1, 1, tzinfo=timezone.utc)


__all__ = ["ApplyOutcome", "ApplyRequest", "ItemPatchRequest", "router"]
