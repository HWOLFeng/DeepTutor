"""SQLite persistence for planning drafts.

One account-wide file, one row per draft, one row per proposed item, and one
snapshot row per applied batch so the batch can be reverted. Nothing here
writes the task board or the calendar: applying a draft is a separate step
(see the planner router), so a draft that is never accepted leaves no trace
outside this file.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable
from uuid import uuid4

from deeptutor.services.planner.models import (
    MAX_DRAFT_ITEMS,
    Decision,
    PlanDraft,
    PlanItem,
    PlanItemInput,
    PlanUndo,
)
from deeptutor.utils.secret_files import ensure_private_directory

#: Drafts are review sessions, not documents: a day is long enough to come
#: back to one, short enough that a stale plan stops being offered.
DEFAULT_TTL_HOURS = 24

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: Fields a reviewer may change before accepting. Decisions are not among
#: them — that is the whole point of ``set_decisions``.
PATCHABLE_FIELDS = frozenset({"title", "due_date", "start_at", "duration_min"})

_DECISIONS = frozenset({"accepted", "dismissed"})
_OPEN_STATUSES = ("drafting", "reviewing")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime | None = None) -> str:
    return (moment or _now()).astimezone(timezone.utc).strftime(STAMP_FORMAT)


def _parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, STAMP_FORMAT).replace(tzinfo=timezone.utc)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class PlanDraftStore:
    """The account's drafts. Every mutation is one SQLite transaction."""

    def __init__(self, path: Path):
        self.path = path
        ensure_private_directory(path.parent)
        self._initialized = False

    # ── plumbing ────────────────────────────────────────────────────────

    def _ensure_schema(self, db: sqlite3.Connection) -> None:
        if self._initialized:
            return
        db.executescript("""
            CREATE TABLE IF NOT EXISTS plan_drafts (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                tzid TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                calendar_revision INTEGER NOT NULL DEFAULT 0,
                board_revision INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT NOT NULL DEFAULT '{}',
                apply_result_json TEXT,
                undo_token TEXT,
                applied_at TEXT
            );
            CREATE INDEX IF NOT EXISTS plan_drafts_session
                ON plan_drafts(session_id, status);
            CREATE TABLE IF NOT EXISTS plan_items (
                draft_id TEXT NOT NULL REFERENCES plan_drafts(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                due_date TEXT,
                start_at TEXT,
                duration_min INTEGER,
                depends_on TEXT,
                conflict TEXT NOT NULL,
                source TEXT NOT NULL,
                locked INTEGER NOT NULL DEFAULT 0,
                decision TEXT NOT NULL DEFAULT 'pending',
                position INTEGER NOT NULL,
                PRIMARY KEY(draft_id, item_id)
            );
            CREATE TABLE IF NOT EXISTS plan_undo (
                token TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                before_json TEXT NOT NULL DEFAULT '{}',
                after_json TEXT NOT NULL DEFAULT '{}',
                task_ids_json TEXT NOT NULL DEFAULT '[]',
                event_ids_json TEXT NOT NULL DEFAULT '[]',
                undone INTEGER NOT NULL DEFAULT 0
            );
        """)
        self._initialized = True

    @contextmanager
    def connect(self, *, write: bool = False):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            self._ensure_schema(connection)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    # ── reading ─────────────────────────────────────────────────────────

    @staticmethod
    def _draft_row(row: sqlite3.Row, items: list[PlanItem]) -> PlanDraft:
        return PlanDraft(
            id=row["id"],
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            tzid=row["tzid"],
            status=row["status"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            calendar_revision=row["calendar_revision"],
            board_revision=row["board_revision"],
            summary=_load(row["summary_json"], {}),
            apply_result=_load(row["apply_result_json"], None),
            undo_token=row["undo_token"],
            applied_at=row["applied_at"],
            items=items,
        )

    @staticmethod
    def _item_row(row: sqlite3.Row) -> PlanItem:
        return PlanItem(
            draft_id=row["draft_id"],
            item_id=row["item_id"],
            kind=row["kind"],
            title=row["title"],
            due_date=row["due_date"],
            start_at=row["start_at"],
            duration_min=row["duration_min"],
            depends_on=row["depends_on"],
            conflict=row["conflict"],
            source=row["source"],
            locked=bool(row["locked"]),
            decision=row["decision"],
            position=row["position"],
        )

    def _items(self, db: sqlite3.Connection, draft_id: str) -> list[PlanItem]:
        rows = db.execute(
            "SELECT * FROM plan_items WHERE draft_id=? ORDER BY position", (draft_id,)
        ).fetchall()
        return [self._item_row(row) for row in rows]

    def _expire_if_due(self, db: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
        """Flips an open draft whose TTL has passed, then returns the row.

        Expiry is derived from ``expires_at`` rather than a timer, so a draft
        read a week later still reports the truth. The write keeps
        ``purge_expired`` and the API agreeing on the same status.
        """
        if row["status"] in _OPEN_STATUSES and _parse_stamp(row["expires_at"]) <= _now():
            db.execute("UPDATE plan_drafts SET status='expired' WHERE id=?", (row["id"],))
            fresh = db.execute("SELECT * FROM plan_drafts WHERE id=?", (row["id"],)).fetchone()
            return fresh
        return row

    def read(self, draft_id: str) -> PlanDraft:
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM plan_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            row = self._expire_if_due(db, row)
            return self._draft_row(row, self._items(db, draft_id))

    def latest_open(self, session_id: str, workspace_id: str) -> PlanDraft | None:
        """The newest draft a reviewer could still act on, if any."""
        with self.connect(write=True) as db:
            rows = db.execute(
                # created_at has one-second resolution, so two drafts made in
                # the same second would otherwise order arbitrarily; rowid is
                # insertion order and keeps "latest" deterministic.
                "SELECT * FROM plan_drafts WHERE session_id=? AND workspace_id=?"
                " ORDER BY created_at DESC, rowid DESC",
                (session_id, workspace_id),
            ).fetchall()
            for row in rows:
                row = self._expire_if_due(db, row)
                if row["status"] in _OPEN_STATUSES:
                    return self._draft_row(row, self._items(db, row["id"]))
            return None

    def read_undo(self, token: str) -> PlanUndo:
        with self.connect() as db:
            row = db.execute("SELECT * FROM plan_undo WHERE token=?", (token,)).fetchone()
        if row is None:
            raise KeyError(token)
        return PlanUndo(
            token=row["token"],
            draft_id=row["draft_id"],
            created_at=row["created_at"],
            before=_load(row["before_json"], {}),
            after=_load(row["after_json"], {}),
            task_ids=_load(row["task_ids_json"], []),
            event_ids=_load(row["event_ids_json"], []),
            undone=bool(row["undone"]),
        )

    # ── writing ─────────────────────────────────────────────────────────

    def create(
        self,
        *,
        session_id: str,
        workspace_id: str,
        tzid: str,
        items: Iterable[PlanItemInput],
        summary: dict[str, Any] | None = None,
        calendar_revision: int = 0,
        board_revision: int = 0,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        draft_id: str | None = None,
    ) -> PlanDraft:
        proposed = list(items)
        if not proposed:
            raise ValueError("A plan draft needs at least one item.")
        if len(proposed) > MAX_DRAFT_ITEMS:
            raise ValueError(f"A plan draft holds at most {MAX_DRAFT_ITEMS} items.")
        identifiers = [f"i{index + 1}" for index in range(len(proposed))]
        kinds = {identifiers[index]: item.kind for index, item in enumerate(proposed)}
        for identifier, item in zip(identifiers, proposed):
            if item.depends_on is None:
                continue
            if item.depends_on not in kinds:
                raise ValueError(f"Item {identifier} depends on an item outside this draft.")
            if kinds[item.depends_on] != "task":
                raise ValueError(f"Item {identifier} must depend on a task item.")
            if item.depends_on == identifier:
                raise ValueError(f"Item {identifier} cannot depend on itself.")

        now = _now()
        created_at = _stamp(now)
        expires_at = _stamp(now + timedelta(hours=max(1, int(ttl_hours))))
        draft_key = draft_id or uuid4().hex
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO plan_drafts (id,session_id,workspace_id,tzid,status,created_at,"
                "expires_at,calendar_revision,board_revision,summary_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_key,
                    session_id,
                    workspace_id,
                    tzid,
                    "reviewing",
                    created_at,
                    expires_at,
                    int(calendar_revision),
                    int(board_revision),
                    _dump(summary or {}),
                ),
            )
            for position, (identifier, item) in enumerate(zip(identifiers, proposed)):
                db.execute(
                    "INSERT INTO plan_items (draft_id,item_id,kind,title,due_date,start_at,"
                    "duration_min,depends_on,conflict,source,locked,decision,position)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        draft_key,
                        identifier,
                        item.kind,
                        item.title,
                        item.due_date,
                        item.start_at,
                        item.duration_min,
                        item.depends_on,
                        item.conflict,
                        item.source,
                        1 if item.locked else 0,
                        "pending",
                        position,
                    ),
                )
            row = db.execute("SELECT * FROM plan_drafts WHERE id=?", (draft_key,)).fetchone()
            return self._draft_row(row, self._items(db, draft_key))

    def set_decisions(self, draft_id: str, decisions: dict[str, str]) -> PlanDraft:
        draft = self.read(draft_id)
        if draft.status not in _OPEN_STATUSES:
            raise ValueError(f"Draft {draft_id} is {draft.status}; decisions are closed.")
        known = {item.item_id for item in draft.items}
        unknown = set(decisions) - known
        if unknown:
            raise KeyError(sorted(unknown)[0])
        for item_id, decision in decisions.items():
            if decision not in _DECISIONS:
                raise ValueError(f"Unknown decision {decision!r}.")
            self._set_decision(draft_id, item_id, decision)
        return self.read(draft_id)

    def _set_decision(self, draft_id: str, item_id: str, decision: Decision) -> None:
        with self.connect(write=True) as db:
            cursor = db.execute(
                "UPDATE plan_items SET decision=? WHERE draft_id=? AND item_id=?",
                (decision, draft_id, item_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(item_id)

    def patch_item(self, draft_id: str, item_id: str, changes: dict[str, Any]) -> PlanDraft:
        if not changes:
            return self.read(draft_id)
        invalid = set(changes) - PATCHABLE_FIELDS
        if invalid:
            raise ValueError(f"Field {sorted(invalid)[0]!r} cannot be changed on a draft item.")
        draft = self.read(draft_id)
        if draft.status not in _OPEN_STATUSES:
            raise ValueError(f"Draft {draft_id} is {draft.status}; items are read-only.")
        if item_id not in {item.item_id for item in draft.items}:
            raise KeyError(item_id)
        columns = sorted(changes)
        assignments = ", ".join(f"{column}=?" for column in columns)
        values = [changes[column] for column in columns]
        with self.connect(write=True) as db:
            db.execute(
                f"UPDATE plan_items SET {assignments} WHERE draft_id=? AND item_id=?",  # nosec B608
                (*values, draft_id, item_id),
            )
        return self.read(draft_id)

    def mark_applied(
        self, draft_id: str, result: dict[str, Any], undo_token: str
    ) -> tuple[PlanDraft, bool]:
        """Records one applied batch. The second call is a no-op replay."""
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM plan_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            row = self._expire_if_due(db, row)
            if row["status"] == "applied":
                return self._draft_row(row, self._items(db, draft_id)), False
            if row["status"] not in _OPEN_STATUSES:
                raise ValueError(f"Draft {draft_id} is {row['status']}; it cannot be applied.")
            db.execute(
                "UPDATE plan_drafts SET status='applied', apply_result_json=?, undo_token=?,"
                " applied_at=? WHERE id=?",
                (_dump(result), undo_token, _stamp(), draft_id),
            )
            fresh = db.execute("SELECT * FROM plan_drafts WHERE id=?", (draft_id,)).fetchone()
            return self._draft_row(fresh, self._items(db, draft_id)), True

    def record_undo(
        self,
        token: str,
        draft_id: str,
        *,
        before: dict[str, Any],
        after: dict[str, Any],
        task_ids: list[str],
        event_ids: list[str],
    ) -> PlanUndo:
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR REPLACE INTO plan_undo (token,draft_id,created_at,before_json,"
                "after_json,task_ids_json,event_ids_json,undone) VALUES (?,?,?,?,?,?,?,0)",
                (
                    token,
                    draft_id,
                    _stamp(),
                    _dump(before),
                    _dump(after),
                    _dump(list(task_ids)),
                    _dump(list(event_ids)),
                ),
            )
        return self.read_undo(token)

    def mark_undone(self, token: str) -> PlanUndo:
        with self.connect(write=True) as db:
            row = db.execute("SELECT * FROM plan_undo WHERE token=?", (token,)).fetchone()
            if row is None:
                raise KeyError(token)
            db.execute("UPDATE plan_undo SET undone=1 WHERE token=?", (token,))
            db.execute("UPDATE plan_drafts SET status='undone' WHERE id=?", (row["draft_id"],))
        return self.read_undo(token)

    def purge_expired(self, now: datetime | None = None) -> int:
        """Drops closed drafts whose TTL has passed. Never touches applied ones."""
        moment = now or _now()
        with self.connect(write=True) as db:
            rows = db.execute(
                "SELECT id, status, expires_at FROM plan_drafts WHERE status IN"
                " ('drafting','reviewing','expired')"
            ).fetchall()
            doomed = [row["id"] for row in rows if _parse_stamp(row["expires_at"]) <= moment]
            for draft_id in doomed:
                db.execute("DELETE FROM plan_items WHERE draft_id=?", (draft_id,))
                db.execute("DELETE FROM plan_drafts WHERE id=?", (draft_id,))
            return len(doomed)


_STORES: dict[str, PlanDraftStore] = {}


def get_plan_draft_store() -> PlanDraftStore:
    """The authenticated account's draft store."""
    from deeptutor.multi_user.paths import get_account_path_service

    path = get_account_path_service().get_runtime_state_dir() / "planner" / "drafts.sqlite"
    key = str(path)
    store = _STORES.get(key)
    if store is None:
        store = PlanDraftStore(path)
        _STORES[key] = store
    return store


__all__ = [
    "DEFAULT_TTL_HOURS",
    "PATCHABLE_FIELDS",
    "PlanDraftStore",
    "get_plan_draft_store",
]
