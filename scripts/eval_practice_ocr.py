"""Measure the practice image importer against a folder of real photographs.

The importer is probabilistic, so "it looks better now" is not a result. This
script runs the same code path the API uses (``preview_images``), writes the
rows it produced, and prints the defects that matter for a mistake notebook:

* a question that already contains its own answer (handwriting leaked in),
* the learner's answer equal to the correct answer (a *false* mistake),
* two questions on one page that are the same question,
* a choice question with fewer than two options.

Usage (inside the app container or a checkout with the runtime deps):

    python scripts/eval_practice_ocr.py --images data/eval/practice-ocr/raw
    python scripts/eval_practice_ocr.py --images DIR --out data/eval/practice-ocr/last.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _load(paths: list[Path]) -> list[tuple[bytes, str]]:
    return [(path.read_bytes(), path.name) for path in paths]


def _row_flags(row: dict, leaked: bool, duplicate: bool) -> list[str]:
    flags: list[str] = []
    if leaked:
        flags.append("answer_in_question")
    if duplicate:
        flags.append("duplicate_stem")
    kind = str(row.get("question_type") or "")
    if kind in {"single_choice", "multi_choice"} and len(row.get("options") or {}) < 2:
        flags.append("choice_without_options")
    if not str(row.get("correct_answer") or "").strip():
        flags.append("no_correct_answer")
    if not str(row.get("explanation") or "").strip():
        flags.append("no_explanation")
    if str(row.get("attempt") or "") == "correct":
        flags.append("answered_correctly")
    if row.get("answer_conflict"):
        flags.append("answer_conflict")
    if row.get("leaks"):
        flags.append("blank_restored")
    return flags


def _leaks(row: dict) -> bool:
    """Whether the answer text already sits in the question stem."""
    from deeptutor.services.practice.quality import answer_in_question

    return answer_in_question(
        str(row.get("question") or ""),
        str(row.get("correct_answer") or ""),
        str(row.get("user_answer") or ""),
        row.get("question_type") or "",
        row.get("options") or {},
    )


def _figure_key(row: dict) -> tuple:
    """The figure a row is about, so two cube-net rows are not "duplicates"."""
    key = []
    for image in row.get("question_images") or []:
        box = image.get("_box")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            key.append(tuple(round(float(value), 2) for value in box))
        elif image.get("_redrawn"):
            key.append(("redrawn", str(image.get("filename") or "")))
    return tuple(key)


def _duplicates(rows: list[dict]) -> set[int]:
    from deeptutor.services.practice.quality import question_fingerprint

    seen: dict[tuple, int] = {}
    dupes: set[int] = set()
    for index, row in enumerate(rows):
        key = (question_fingerprint(str(row.get("question") or "")), _figure_key(row))
        if key in seen:
            dupes.add(index)
        else:
            seen[key] = index
    return dupes


def report(name: str, parsed: dict, elapsed: float) -> dict:
    rows = parsed["questions"]
    errors = parsed.get("errors") or []
    dupes = _duplicates(rows)
    flagged = [
        {**row, "flags": _row_flags(row, _leaks(row), index in dupes)}
        for index, row in enumerate(rows)
    ]
    counts = {
        "questions": len(rows),
        "errors": len(errors),
        "with_answer_in_question": sum(1 for r in flagged if "answer_in_question" in r["flags"]),
        "answered_correctly": sum(1 for r in flagged if "answered_correctly" in r["flags"]),
        "duplicate_stem": len(dupes),
        "choice_without_options": sum(
            1 for r in flagged if "choice_without_options" in r["flags"]
        ),
        "with_figure": sum(1 for r in rows if r.get("question_images")),
        "with_user_answer": sum(1 for r in rows if str(r.get("user_answer") or "").strip()),
    }
    return {
        "image": name,
        "elapsed_s": round(elapsed, 1),
        "counts": counts,
        "rows": [
            {
                "question": row.get("question"),
                "question_type": row.get("question_type"),
                "options": row.get("options"),
                "correct_answer": row.get("correct_answer"),
                "user_answer": row.get("user_answer"),
                "explanation": row.get("explanation"),
                "attempt": row.get("attempt"),
                "answer_source": row.get("answer_source"),
                "answer_conflict": row.get("answer_conflict"),
                "verdict_answer": row.get("verdict_answer"),
                "leaks": row.get("leaks"),
                "figures": [
                    {"filename": image.get("filename"), "mime_type": image.get("mime_type")}
                    for image in (row.get("question_images") or [])
                ],
                "flags": row["flags"],
            }
            for row in flagged
        ],
        "errors": errors,
    }


async def run(images: list[Path], out: Path | None) -> int:
    from deeptutor.services.practice.document_import import preview_images

    payload = _load(images)
    started = time.monotonic()
    parsed = await preview_images(payload)
    elapsed = time.monotonic() - started
    # ``preview_images`` handles a batch of pages as one import; the report
    # keeps the batch view (row order follows the images).
    result = report(" + ".join(path.name for path in images), parsed, elapsed)
    print(json.dumps(result["counts"], ensure_ascii=False, indent=2))
    for index, row in enumerate(result["rows"], 1):
        flags = ",".join(row["flags"]) or "-"
        print(f"\n[{index}] ({flags}) {row['question_type']}  {row['question'][:160]}")
        if row["options"]:
            print("   options:", json.dumps(row["options"], ensure_ascii=False)[:200])
        print(f"   UA={row['user_answer']!r}  CA={row['correct_answer']!r}")
        if row["figures"]:
            print("   figures:", [figure["filename"] for figure in row["figures"]])
    if result["errors"]:
        print("\nerrors:", json.dumps(result["errors"], ensure_ascii=False, indent=2))
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True, help="folder or single image")
    parser.add_argument("--out", type=Path, default=None, help="write the full report here")
    args = parser.parse_args()
    paths = (
        sorted(
            path
            for path in args.images.iterdir()
            if path.suffix.lower() in SUFFIXES and path.is_file()
        )
        if args.images.is_dir()
        else [args.images]
    )
    if not paths:
        parser.error(f"no images under {args.images}")
    return asyncio.run(run(paths, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
