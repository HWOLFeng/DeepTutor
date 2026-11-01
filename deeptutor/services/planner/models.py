"""Models for one planning draft and its items.

The draft is the only artifact a plan generator may write. Items carry the
decisions the learner makes in the review card; nothing here knows how tasks
or events are stored, so the models can be exercised without a workspace.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

DraftStatus = Literal["drafting", "reviewing", "applied", "undone", "expired"]
Decision = Literal["pending", "accepted", "dismissed"]
ItemKind = Literal["task", "block", "review"]
Conflict = Literal["none", "duplicate", "overlap"]
ItemSource = Literal["plan", "srs"]

#: A draft never holds more than this many items: a plan a learner will not
#: read is a plan they will not accept.
MAX_DRAFT_ITEMS = 60

PlanTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


def _calendar_date(value: str) -> str:
    date.fromisoformat(value)  # rejects 2026-02-30 after the shape check passes
    return value


#: A plain local calendar day, never converted through UTC.
PlanDueDate = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_calendar_date),
]


class PlanItemInput(BaseModel):
    """What a generator proposes for one row of the review card."""

    model_config = ConfigDict(extra="forbid")

    kind: ItemKind
    title: PlanTitle
    due_date: PlanDueDate | None = None
    start_at: str | None = None
    duration_min: int | None = Field(default=None, ge=1, le=600)
    #: item_id of the task this time block serves. Blocks without one are
    #: dropped before they reach the store — an orphan block has no meaning.
    depends_on: str | None = None
    conflict: Conflict = "none"
    source: ItemSource = "plan"
    #: Locked items (spaced repetition) are accepted by default and cannot be
    #: dismissed; only their date may move.
    locked: bool = False

    @field_validator("start_at")
    @classmethod
    def _blank_start_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class PlanItem(PlanItemInput):
    """A proposed item as stored, with its decision and stable position."""

    draft_id: str
    item_id: str
    decision: Decision = "pending"
    #: An optional tag explaining a dismissal ("time", "topic", "duplicate").
    #: Kept so a rejection can be understood later, never to gate the UI.
    reason: str | None = None
    position: int


class PlanDraft(BaseModel):
    """A reviewable plan, scoped to one conversation and one workspace."""

    id: str
    session_id: str
    workspace_id: str
    tzid: str
    status: DraftStatus
    created_at: str
    expires_at: str
    calendar_revision: int = 0
    board_revision: int = 0
    #: Free-form rendering facts (goal label, weekly limit, counts) the review
    #: card needs but the models do not: everything is optional by design.
    summary: dict[str, Any] = Field(default_factory=dict)
    apply_result: dict[str, Any] | None = None
    undo_token: str | None = None
    applied_at: str | None = None
    items: list[PlanItem] = Field(default_factory=list)

    def accepted(self) -> list[PlanItem]:
        return [item for item in self.items if item.decision == "accepted"]

    def dismissed(self) -> list[PlanItem]:
        return [item for item in self.items if item.decision == "dismissed"]


class PlanUndo(BaseModel):
    """The snapshot one ``apply`` leaves behind so the batch can be reverted."""

    token: str
    draft_id: str
    created_at: str
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    task_ids: list[str] = Field(default_factory=list)
    event_ids: list[str] = Field(default_factory=list)
    undone: bool = False


__all__ = [
    "MAX_DRAFT_ITEMS",
    "Conflict",
    "Decision",
    "DraftStatus",
    "ItemKind",
    "ItemSource",
    "PlanDraft",
    "PlanDueDate",
    "PlanItem",
    "PlanItemInput",
    "PlanUndo",
    "PlanTitle",
]
