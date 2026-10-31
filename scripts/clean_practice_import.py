"""Remove one import batch from a practice database, safely.

An import writes into four places: ``notebook_entries``, its category links,
the review state/events keyed by entry, and the staged ``practice_imports``
receipt; the figures live as files under the workspace's attachment folder.
Deleting only the rows leaves orphan files behind, and deleting only the files
leaves questions pointing at a missing image. This script removes all of them
for one import and refuses to guess: it runs dry by default, prints exactly
what it would delete, and copies the database before touching it.

Usage:
    python scripts/clean_practice_import.py --db data/users/<u>/user/chat_history.db \
        --material-id import:57fe7e97965d4aeaaf078c4837f5eaf7
    # add --apply to actually delete, --purge-files to remove the figure files
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import sys


def _backup(db: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = db.with_name(f"{db.name}.bak-{stamp}")
    shutil.copy2(db, target)
    return target


def _entries(conn: sqlite3.Connection, material_id: str) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(
        conn.execute(
            """SELECT id, question, material_id, material_title, question_images_json
               FROM notebook_entries WHERE material_id = ? ORDER BY id""",
            (material_id,),
        )
    )


def _figure_files(root: Path, image_ids: set[str]) -> list[Path]:
    """Attachments belonging to the import, matched by their id prefix."""
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or "practice-import" not in path.parts:
            continue
        if any(path.name.startswith(f"{image_id}_") for image_id in image_ids):
            found.append(path)
    return found


def clean(
    db: Path,
    material_ids: list[str],
    tokens: list[str],
    *,
    apply: bool,
    purge_files: bool,
    data_root: Path,
) -> int:
    entries_total, files_total = 0, 0
    for material_id in material_ids:
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            rows = _entries(conn, material_id)
            if not rows:
                print(f"no entries for {material_id}")
                continue
            ids = [int(row["id"]) for row in rows]
            image_ids: set[str] = set()
            for row in rows:
                try:
                    images = json.loads(row["question_images_json"] or "[]")
                except ValueError:
                    continue
                for image in images:
                    image_id = str((image or {}).get("id") or "")
                    if image_id:
                        image_ids.add(image_id)
            files = _figure_files(data_root, image_ids) if image_ids else []
            print(f"\n{material_id}")
            print(f"  questions : {len(ids)} (ids {ids[0]}..{ids[-1]})")
            print(f"  first     : {rows[0]['question'][:60]!r}")
            print(f"  figures   : {len(files)} files")
            if not apply:
                for path in files[:5]:
                    print(f"    - {path.name}")
                continue
            conn.execute("BEGIN")
            conn.executemany(
                "DELETE FROM practice_review_events WHERE entry_id = ?", [(i,) for i in ids]
            )
            conn.executemany(
                "DELETE FROM practice_review_state WHERE entry_id = ?", [(i,) for i in ids]
            )
            conn.executemany(
                "DELETE FROM notebook_entry_categories WHERE entry_id = ?", [(i,) for i in ids]
            )
            conn.executemany("DELETE FROM notebook_entries WHERE id = ?", [(i,) for i in ids])
            conn.execute("DELETE FROM practice_imports WHERE token = ?", (material_id.split(":", 1)[-1],))
            conn.commit()
            entries_total += len(ids)
            if purge_files:
                for path in files:
                    path.unlink(missing_ok=True)
                files_total += len(files)
        finally:
            conn.close()

    if tokens:
        conn = sqlite3.connect(db)
        try:
            for token in tokens:
                if apply:
                    cursor = conn.execute("DELETE FROM practice_imports WHERE token = ?", (token,))
                    print(f"staged import {token[:8]}: removed {cursor.rowcount}")
                else:
                    row = conn.execute(
                        "SELECT filename, target FROM practice_imports WHERE token = ?", (token,)
                    ).fetchone()
                    print(f"staged import {token[:8]}: would remove ({row[0] if row else 'not found'})")
            if apply:
                conn.commit()
        finally:
            conn.close()

    if not apply:
        print("\ndry run — nothing deleted. Re-run with --apply to delete.")
        return 0
    print(f"\ndeleted {entries_total} questions, {files_total} figure files")
    return 0


def prune_orphans(db: Path, data_root: Path, *, apply: bool) -> int:
    """Attachments written by a preview that was never committed.

    Figures are stored while the preview is built, so an abandoned preview
    leaves files no entry points at. Only the ``practice-import`` pseudo-session
    is in scope: that folder exists solely for imports.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        referenced: set[str] = set()
        for (raw,) in conn.execute("SELECT question_images_json FROM notebook_entries"):
            try:
                images = json.loads(raw or "[]")
            except ValueError:
                continue
            for image in images:
                image_id = str((image or {}).get("id") or "")
                if image_id:
                    referenced.add(image_id)
    finally:
        conn.close()
    orphans = [
        path
        for path in data_root.rglob("*")
        if path.is_file()
        and "practice-import" in path.parts
        and path.name.split("_", 1)[0] not in referenced
    ]
    print(f"orphan figures: {len(orphans)} (referenced {len(referenced)})")
    for path in orphans[:5]:
        print(f"  - {path.name}")
    if apply:
        for path in orphans:
            path.unlink(missing_ok=True)
    return len(orphans)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="the user's chat_history.db")
    parser.add_argument(
        "--material-id",
        action="append",
        default=[],
        help="material_id of the import (import:<token>), repeatable",
    )
    parser.add_argument(
        "--token", action="append", default=[], help="a staged import token to drop, repeatable"
    )
    parser.add_argument("--data-root", type=Path, default=Path("data"), help="where attachments live")
    parser.add_argument("--purge-files", action="store_true", help="delete the figure files too")
    parser.add_argument(
        "--prune-orphans", action="store_true", help="also delete figures no entry references"
    )
    parser.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()

    if not args.db.is_file():
        parser.error(f"database not found: {args.db}")
    if not args.material_id and not args.token and not args.prune_orphans:
        parser.error("give at least one --material-id, --token or --prune-orphans")
    # A dry run must not even create a backup: the repository's data folder is
    # a live deployment, and a stray copy is one more thing to explain later.
    if args.apply:
        print(f"backup: {_backup(args.db)}")
    clean(
        args.db,
        args.material_id,
        args.token,
        apply=args.apply,
        purge_files=args.purge_files,
        data_root=args.data_root,
    )
    if args.prune_orphans:
        prune_orphans(args.db, args.data_root, apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
