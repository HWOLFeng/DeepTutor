"""Practice workflow. Every operation resolves the current user's scoped store."""

from __future__ import annotations

import asyncio
import contextvars
import csv
import io
import logging
from pathlib import Path
import threading
import time
from typing import Literal
import uuid
import zipfile

from defusedxml.ElementTree import ParseError
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field

from deeptutor.services.practice.answers import check_answer
from deeptutor.services.practice.importing import MAX_BYTES, preview
from deeptutor.services.practice.scheduler import Rating, day_bounds
from deeptutor.services.practice.storage import PracticeStore, ReviewConflict
from deeptutor.services.session import get_sqlite_session_store

from .question_notebook import NotebookEntryItem

router = APIRouter()

logger = logging.getLogger(__name__)


class PracticeSummary(BaseModel):
    total: int
    mistakes: int
    due: int
    overdue: int
    reviewed_today: int
    next_due_at: float | None
    day_end: float
    timezone: str


class PracticeQuestion(BaseModel):
    entry: NotebookEntryItem
    state: dict
    content_workspace_id: str = ""
    content_workspace_name: str = ""


class PracticeCounts(BaseModel):
    questions: int
    mistakes: int
    reviews: int


class PracticeDailyCounts(PracticeCounts):
    date: str


class PracticeSourceCounts(PracticeCounts):
    source: str


class PracticeAnalytics(BaseModel):
    timezone: str
    days: int
    start_date: str
    end_date: str
    updated_at: float
    daily: list[PracticeDailyCounts]
    sources: list[PracticeSourceCounts]
    totals: PracticeCounts


class AnswerRequest(BaseModel):
    answer: str = Field(default="", max_length=20000)


class ReviewRequest(AnswerRequest):
    self_report: bool = False
    request_id: str = Field(min_length=16, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    version: int = Field(ge=0)
    rating: Rating


class CommitRequest(BaseModel):
    token: str = Field(min_length=32, max_length=32, pattern=r"^[a-f0-9]+$")
    #: Indices into the preview's ``questions`` list. ``None`` imports all of
    #: them, which is what a client that never showed a selection expects.
    selected: list[int] | None = Field(default=None, max_length=500)


def _validate_timezone(timezone: str) -> tuple[float, float]:
    try:
        return day_bounds(timezone, time.time())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _entry(entry_id: int) -> dict:
    store = get_sqlite_session_store()
    if not await asyncio.to_thread(PracticeStore(store.db_path).is_visible, entry_id):
        raise HTTPException(404, "Question not found")
    entry = await store.get_notebook_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Question not found")
    return entry


@router.get("/summary", response_model=PracticeSummary)
async def summary(timezone: str = "UTC"):
    store = get_sqlite_session_store()
    try:
        return await asyncio.to_thread(PracticeStore(store.db_path).overview, timezone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/queue", response_model=list[PracticeQuestion])
async def queue(
    timezone: str = "UTC",
    category_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
):
    store = get_sqlite_session_store()
    practice = PracticeStore(store.db_path)
    try:
        ids = await asyncio.to_thread(practice.queue, timezone, category_id, limit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    from deeptutor.services.workspace.context import current_workspace_id

    questions = []
    for entry_id in ids:
        entry = await store.get_notebook_entry(entry_id)
        if entry:
            questions.append(
                {
                    "entry": entry,
                    "state": await asyncio.to_thread(practice.state, entry_id),
                    "content_workspace_id": current_workspace_id(),
                }
            )
    return questions


@router.get("/analytics", response_model=PracticeAnalytics)
async def practice_analytics(timezone: str = "UTC", days: int = 30):
    _validate_timezone(timezone)
    if days not in {7, 30, 90}:
        raise HTTPException(422, "Days must be 7, 30, or 90")
    store = get_sqlite_session_store()
    try:
        return await asyncio.to_thread(PracticeStore(store.db_path).analytics, timezone, days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/questions/{entry_id}", response_model=PracticeQuestion)
async def practice_question(entry_id: int):
    entry = await _entry(entry_id)
    state = await asyncio.to_thread(
        PracticeStore(get_sqlite_session_store().db_path).state, entry_id
    )
    return {"entry": entry, "state": state}


@router.post("/questions/{entry_id}/check")
async def check(entry_id: int, payload: AnswerRequest):
    entry = await _entry(entry_id)
    return {"correct": check_answer(entry, payload.answer)}


@router.post("/questions/{entry_id}/review")
async def review(entry_id: int, payload: ReviewRequest):
    try:
        return await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).review,
            entry_id,
            payload.request_id,
            payload.version,
            payload.rating,
            payload.answer,
            self_report=payload.self_report,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/import/template")
async def import_template(format: Literal["csv", "xlsx"] = "csv"):
    rows = [
        [
            "question",
            "question_type",
            "A",
            "B",
            "C",
            "D",
            "correct_answer",
            "explanation",
            "tags",
            "user_answer",
        ],
        ["2 + 2 = ?", "single_choice", "3", "4", "5", "6", "B", "2 + 2 = 4", "Math,Addition", ""],
        [
            "Select the prime numbers",
            "multi_choice",
            "2",
            "3",
            "4",
            "6",
            "A,B",
            "2 and 3 are prime",
            "Math",
            "",
        ],
        ["The Earth orbits the Sun", "true_false", "", "", "", "", "true", "", "Science", ""],
        [
            "Explain photosynthesis",
            "short_answer",
            "",
            "",
            "",
            "",
            "Plants convert light into chemical energy.",
            "",
            "Biology",
            "",
        ],
    ]
    if format == "xlsx":
        from openpyxl import Workbook

        book = Workbook()
        for row in rows:
            book.active.append(row)
        output = io.BytesIO()
        book.save(output)
        book.close()
        data = output.getvalue()
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        data, media = output.getvalue().encode("utf-8-sig"), "text/csv"
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="practice-template.{format}"'},
    )


_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _is_image_name(name: str) -> bool:
    return Path(name).suffix.lower() in _IMAGE_SUFFIXES


def _payload_limits(images_only: bool) -> int:
    from deeptutor.services.practice.document_import import MAX_IMAGE_IMPORT_BYTES

    return MAX_IMAGE_IMPORT_BYTES if images_only else MAX_BYTES


def _validate_uploads(uploads: list[UploadFile]) -> bool:
    if not uploads:
        raise HTTPException(422, "Select a file or images to import")
    images_only = all(_is_image_name(upload.filename or "") for upload in uploads)
    if len(uploads) > 1 and not images_only:
        raise HTTPException(
            422, "When importing several files at once, all of them must be images"
        )
    return images_only


async def _read_uploads(
    uploads: list[UploadFile], images_only: bool
) -> list[tuple[bytes, str]]:
    from deeptutor.services.practice.document_import import MAX_IMAGE_IMPORT_BYTES

    limit = MAX_IMAGE_IMPORT_BYTES if images_only else MAX_BYTES
    payload: list[tuple[bytes, str]] = []
    for upload in uploads:
        payload.append((await upload.read(limit + 1), upload.filename or "image.png"))
    return payload


async def _parse_payload(
    payload: list[tuple[bytes, str]],
    *,
    images_only: bool,
    target: str,
    label: str,
    on_progress=None,
) -> tuple[dict, str]:
    """Turn uploaded bytes into rows. Shared by the sync and job endpoints."""
    if images_only:
        from deeptutor.services.practice.document_import import preview_images

        parsed = await preview_images(payload, on_progress=on_progress)
        return parsed, label.strip()[:160] or _group_label([name for _, name in payload])
    data, filename = payload[0]
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix in {"pdf", "docx"}:
        from deeptutor.services.practice.document_import import preview_document

        return await preview_document(data, filename, on_progress=on_progress), filename
    return await asyncio.to_thread(preview, data, filename), filename


def _group_label(names: list[str]) -> str:
    if len(names) == 1:
        return names[0] or "image.png"
    first = Path(names[0] or "image.png").name
    return f"{first} and {len(names) - 1} more"


def _preview_payload(parsed: dict, token: str | None) -> dict:
    return {
        "token": token,
        "total": len(parsed["questions"]) + len(parsed["errors"]),
        "valid": len(parsed["questions"]),
        "errors": parsed["errors"],
        # The full list, not a sample: OCR is probabilistic, so the learner
        # has to be able to uncheck the rows that came out wrong. ``samples``
        # stays for older clients that only render a preview strip.
        "questions": parsed["questions"],
        "samples": parsed["questions"][:5],
        "duplicates_merged": parsed.get("duplicates_merged", 0),
    }


@router.post("/import/preview")
async def import_preview(
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    target: Literal["bank", "mistakes"] = Form("bank"),
    label: str = Form(""),
):
    """Preview one table/PDF/DOCX file, or a batch of question images.

    Several images arrive as ``files`` and become one staged import, so the
    sidebar aggregates them as a single group instead of one row per photo.
    """
    uploads = ([file] if file is not None else []) + list(files or [])
    images_only = _validate_uploads(uploads)
    try:
        payload = await _read_uploads(uploads, images_only)
        parsed, filename = await _parse_payload(
            payload, images_only=images_only, target=target, label=label
        )
    except (ValueError, UnicodeError, OSError, zipfile.BadZipFile, KeyError, ParseError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        for upload in uploads:
            await upload.close()
    token = None
    if not parsed["errors"]:
        await _persist_question_images(parsed["questions"])
        token = await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).stage_import,
            filename,
            target,
            parsed["questions"],
        )
    return _preview_payload(parsed, token)


@router.post("/import/jobs")
async def start_import_job(
    file: UploadFile | None = File(None),
    files: list[UploadFile] | None = File(None),
    target: Literal["bank", "mistakes"] = Form("bank"),
    label: str = Form(""),
):
    """Start a preview in the background and return a job to poll.

    A scanned handout takes tens of seconds per page; holding the request open
    gives the browser nothing to show. The worker writes its progress into the
    job row, so a reload mid-import picks the number back up.
    """
    uploads = ([file] if file is not None else []) + list(files or [])
    images_only = _validate_uploads(uploads)
    try:
        payload = await _read_uploads(uploads, images_only)
    finally:
        for upload in uploads:
            await upload.close()
    store = get_sqlite_session_store()
    job_id = await asyncio.to_thread(PracticeStore(store.db_path).create_import_job, len(payload))
    # A plain thread does not inherit contextvars, and the attachment store
    # resolves the content workspace from one. Carrying the context across is
    # what keeps the figures in the workspace the learner is looking at.
    context = contextvars.copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(_run_import_job, job_id, store.db_path, payload, images_only, target, label),
        name=f"practice-import-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "total": len(payload)}


@router.get("/import/jobs/{job_id}")
async def import_job(job_id: str):
    store = get_sqlite_session_store()
    job = await asyncio.to_thread(PracticeStore(store.db_path).get_import_job, job_id)
    if job is None:
        raise HTTPException(404, "Import job not found")
    return job


def _run_import_job(
    job_id: str,
    db_path: Path,
    payload: list[tuple[bytes, str]],
    images_only: bool,
    target: str,
    label: str,
) -> None:
    """Worker thread: parse, store figures, stage, and publish progress."""
    practice = PracticeStore(db_path)

    def progress(done: int, total: int, stage: str) -> None:
        practice.update_import_job(
            job_id, progress=done, total=max(1, total), stage=stage, status="running"
        )

    try:
        parsed, filename = asyncio.run(
            _parse_payload(
                payload,
                images_only=images_only,
                target=target,
                label=label,
                on_progress=progress,
            )
        )
        practice.update_import_job(job_id, stage="storing", status="running")
        token = None
        if not parsed["errors"]:
            asyncio.run(_persist_question_images(parsed["questions"]))
            token = practice.stage_import(filename, target, parsed["questions"])
        practice.finish_import_job(job_id, _preview_payload(parsed, token))
    except Exception as exc:  # the browser gets the message, the job gets closed
        logger.warning("practice import job %s failed", job_id, exc_info=True)
        practice.fail_import_job(job_id, str(exc) or exc.__class__.__name__)


async def _persist_question_images(questions: list[dict]) -> None:
    """Move parser image bytes into the AttachmentStore and keep references.

    Imported questions have no chat session, so a stable pseudo-session keeps
    the public URL shape and cleanup semantics identical to chat attachments.
    """
    from deeptutor.services.storage import get_attachment_store

    store = get_attachment_store()
    for question in questions:
        persisted = []
        for image in question.get("question_images") or []:
            data = image.get("data") or b""
            if not data:
                persisted.append(image)
                continue
            attachment_id = uuid.uuid4().hex[:12]
            filename = str(image.get("filename") or "figure.png")
            mime_type = str(image.get("mime_type") or "image/png")
            try:
                url = await store.put(
                    session_id="practice-import",
                    attachment_id=attachment_id,
                    filename=filename,
                    data=data,
                    mime_type=mime_type,
                )
            except (OSError, ValueError) as exc:
                raise ValueError(f"Could not store an imported figure: {exc}") from exc
            persisted.append(
                {
                    "id": attachment_id,
                    "url": url,
                    "filename": filename,
                    "mime_type": mime_type,
                }
            )
        question["question_images"] = persisted


@router.post("/import/commit")
async def import_commit(payload: CommitRequest):
    try:
        return await asyncio.to_thread(
            PracticeStore(get_sqlite_session_store().db_path).commit_import,
            payload.token,
            payload.selected,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
