"""Practice must preserve source evidence while imports/reviews are retry safe."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from deeptutor.api.routers import practice, question_notebook
from deeptutor.core.assessment import ASSESSMENT_SOURCES
from deeptutor.services.practice.importing import normalize_question, preview
from deeptutor.services.practice.scheduler import DAY, day_bounds, schedule
from deeptutor.services.practice.storage import PracticeStore, ReviewConflict
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.fixture
def bank(tmp_path, monkeypatch):
    store = SQLiteSessionStore(tmp_path / "practice.db")
    for module in (practice, question_notebook):
        monkeypatch.setattr(module, "get_sqlite_session_store", lambda: store)
    app = FastAPI()
    app.include_router(question_notebook.router, prefix="/bank")
    app.include_router(practice.router, prefix="/practice")
    with TestClient(app) as client:
        yield store, PracticeStore(store.db_path), client


def seed(store, **changes):
    async def run():
        await store.ensure_notebook_session("lesson", "Lesson")
        item = {
            "question_id": "q1",
            "question": "2 + 2?",
            "question_type": "single_choice",
            "options": {"A": "3", "B": "4"},
            "correct_answer": "B",
            "user_answer": "A",
            "is_correct": False,
            **changes,
        }
        await store.upsert_notebook_entries("lesson", [item])
        return await store.find_notebook_entry("lesson", item["question_id"])

    return asyncio.run(run())


@pytest.mark.parametrize("source", sorted(ASSESSMENT_SOURCES))
def test_every_producer_enrolls_mistakes_once(bank, source):
    store, repo, _ = bank
    entry = seed(store, source=source)
    first = repo.state(entry["id"])
    assert first["due_at"] == entry["created_at"] + DAY
    repo.review(entry["id"], "first_review", first["version"], "good", "B")
    reviewed = repo.state(entry["id"])
    seed(store, source=source)  # idempotent source re-save cannot reset the schedule
    assert repo.state(entry["id"]) == reviewed


def test_ungraded_imports_are_not_mistakes_and_review_preserves_original(bank):
    store, repo, client = bank
    entry = seed(store, result="ungraded")
    assert repo.overview("UTC")["mistakes"] == 0
    payload = {"request_id": "submission_00000001", "version": 0, "rating": "again", "answer": "A"}
    assert client.post(f"/practice/questions/{entry['id']}/review", json=payload).status_code == 200
    assert repo.overview("UTC")["mistakes"] == 1
    original = asyncio.run(store.get_notebook_entry(entry["id"]))
    for field in ("user_answer", "result", "is_correct", "correct_answer", "created_at"):
        assert original[field] == entry[field]
    assert client.get("/bank/entries?mistakes_only=true").json()["total"] == 1


def test_duplicate_review_and_two_tabs_cannot_advance_twice(bank):
    store, repo, _ = bank
    entry = seed(store)
    args = (entry["id"], "same_request", 0, "good", "B")
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: repo.review(*args), range(2)))
    assert results[0] == results[1]
    assert repo.state(entry["id"])["review_count"] == 1
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "other_request", 0, "easy", "B")
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "same_request", 0, "again", "A")


def test_server_rejects_incorrect_answer_with_success_rating(bank):
    store, repo, client = bank
    entry = seed(store)
    response = client.post(
        f"/practice/questions/{entry['id']}/review",
        json={"request_id": "submission_00000001", "version": 0, "rating": "good", "answer": "A"},
    )
    assert response.status_code == 422
    assert repo.state(entry["id"])["review_count"] == 0


def test_source_key_change_invalidates_open_question(bank):
    store, repo, _ = bank
    entry = seed(store)
    seed(store, correct_answer="A")
    with pytest.raises(ReviewConflict):
        repo.review(entry["id"], "stale", 0, "good", "A")


def test_timezone_due_order_and_resolved_entries(bank):
    store, repo, client = bank
    old = seed(store)
    new = seed(store, question_id="q2")
    with repo.connect() as conn:
        conn.execute("UPDATE practice_review_state SET due_at=100 WHERE entry_id=?", (old["id"],))
        conn.execute("UPDATE practice_review_state SET due_at=200 WHERE entry_id=?", (new["id"],))
    assert repo.queue("Asia/Shanghai", now=DAY) == [old["id"], new["id"]]
    asyncio.run(store.update_notebook_entry(old["id"], {"resolved": True}))
    assert repo.queue("UTC", now=DAY) == [new["id"]]
    assert repo.overview("UTC", now=DAY)["mistakes"] == 2
    with repo.connect() as conn:
        conn.execute("UPDATE sessions SET deleted_at=1 WHERE id='lesson'")
    assert repo.overview("UTC", now=DAY)["total"] == 0
    assert repo.queue("UTC", now=DAY) == []
    assert client.get(f"/practice/questions/{old['id']}").status_code == 404
    assert (
        client.post(f"/practice/questions/{old['id']}/check", json={"answer": "B"}).status_code
        == 404
    )


def test_first_mistake_timestamp_follows_first_failed_practice(bank):
    store, repo, _ = bank
    entry = seed(store, result="ungraded")
    repo.review(entry["id"], "correct_first", 0, "good", "B", now=100)
    repo.review(entry["id"], "wrong_later", 1, "again", "A", now=200)
    assert repo.state(entry["id"])["first_wrong_at"] == 200


def test_source_mistake_after_success_uses_the_actual_wrong_date(bank):
    store, repo, _ = bank
    entry = seed(store, result="ungraded")
    repo.review(entry["id"], "initial-success", 0, "good", "B", now=100)
    with repo.connect() as conn:
        conn.execute(
            "UPDATE notebook_entries SET result='incorrect', is_correct=0, updated_at=200 WHERE id=?",
            (entry["id"],),
        )
    assert repo.state(entry["id"])["first_wrong_at"] == 200


def test_oversized_spreadsheet_dimensions_and_csv_cells_are_rejected(bank):
    _, _, client = bank
    book = Workbook()
    book.active.cell(row=1, column=1000, value="question")
    output = io.BytesIO()
    book.save(output)
    book.close()
    for name, data in [
        ("wide.xlsx", output.getvalue()),
        ("large.csv", b"question,answer\n" + b"a" * 200000 + b",yes\n"),
    ]:
        response = client.post("/practice/import/preview", files={"file": (name, data)})
        assert response.status_code == 422


def test_import_preview_commit_retries_dedup_tags_and_no_source_overwrite(bank):
    store, repo, client = bank
    data = "题目,题型,A,B,答案,标签\n2+2=?,单选题,3,4,B,数学\n".encode()
    result = client.post(
        "/practice/import/preview", files={"file": ("questions.csv", data)}, data={"target": "bank"}
    ).json()
    assert result["valid"] == 1 and result["token"]
    assert repo.overview("UTC")["total"] == 0  # preview does not alter the bank
    commit = client.post("/practice/import/commit", json={"token": result["token"]})
    assert commit.json() == {"created": 1, "duplicates": 0, "total": 1, "mistakes": 0}
    assert (
        client.post("/practice/import/commit", json={"token": result["token"]}).json()
        == commit.json()
    )
    questions = [
        normalize_question({"question": "2+2=?", "A": "3", "B": "4", "answer": "B", "tags": "复习"})
    ]
    again = repo.commit_import(repo.stage_import("again.json", "mistakes", questions))
    assert again["duplicates"] == 1
    listing = asyncio.run(store.list_notebook_entries())
    assert listing["total"] == 1
    entry = listing["items"][0]
    assert entry["result"] == "ungraded"
    assert {category["name"] for category in entry["categories"]} == {"数学", "复习"}
    assert repo.overview("UTC")["mistakes"] == 1


def test_invalid_row_blocks_entire_import_and_reports_line(bank):
    _, repo, client = bank
    result = client.post(
        "/practice/import/preview",
        files={"file": ("file.csv", b"question,answer\ngood,yes\nbad,\n")},
    ).json()
    assert result["token"] is None
    assert result["errors"][0]["row"] == 3
    assert result["valid"] == 1
    assert repo.overview("UTC")["total"] == 0


def test_template_round_trips_and_formulas_are_rejected(bank):
    _, _, client = bank
    for format in ("csv", "xlsx"):
        data = client.get(f"/practice/import/template?format={format}").content
        assert len(preview(data, f"template.{format}")["questions"]) == 4
    book = Workbook()
    book.active.append(["question", "answer"])
    book.active.append(["=1+1", "2"])
    file = io.BytesIO()
    book.save(file)
    with pytest.raises(ValueError, match="formulas"):
        preview(file.getvalue(), "formulas.xlsx")


def test_multi_select_text_and_json_validation():
    row = normalize_question(
        {"题干": "Primes?", "题型": "多选题", "options": ["2", "3", "4"], "答案": "AB"}
    )
    assert row["correct_answer"] == "A,B"
    assert preview(json.dumps([row]).encode(), "q.json")["questions"][0]["question"] == "Primes?"
    with pytest.raises(ValueError, match="500"):
        preview(json.dumps([row] * 501).encode(), "many.json")


def test_additive_migration_is_idempotent(bank):
    store, repo, _ = bank
    entry = seed(store)
    with repo.connect() as conn:
        for trigger in (
            "practice_capture_insert",
            "practice_capture_update",
            "practice_invalidate_question",
        ):
            conn.execute(f"DROP TRIGGER {trigger}")
        conn.execute("DROP TABLE practice_review_state")
    upgraded = SQLiteSessionStore(store.db_path)
    state = repo.state(entry["id"])
    assert state["first_wrong_at"] == entry["created_at"]
    restored = asyncio.run(upgraded.get_notebook_entry(entry["id"]))
    assert {k: v for k, v in restored.items() if k not in {"practice", "categories"}} == entry
    SQLiteSessionStore(store.db_path)
    assert repo.state(entry["id"]) == state
    asyncio.run(upgraded.delete_notebook_entry(entry["id"]))
    with repo.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM practice_review_state").fetchone()[0] == 0


def test_import_transaction_rolls_back(bank):
    _, repo, _ = bank
    token = repo.stage_import(
        "q.json", "bank", [normalize_question({"question": "Q", "answer": "A"})]
    )
    with repo.connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON notebook_entries BEGIN SELECT RAISE(ABORT, 'unavailable'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        repo.commit_import(token)
    assert repo.overview("UTC")["total"] == 0
    with repo.connect() as conn:
        conn.execute("DROP TRIGGER reject_import")
    assert repo.commit_import(token)["created"] == 1


def test_daily_scheduler_and_dst():
    now = datetime(2026, 3, 8, 12, tzinfo=timezone.utc).timestamp()
    start, end = day_bounds("America/New_York", now)
    assert end - start == 23 * 3600
    with pytest.raises(ValueError):
        day_bounds("not/a-zone", now)
    good = schedule({}, "good", now)
    better = schedule(good, "good", good["due_at"])
    again = schedule(better, "again", better["due_at"])
    assert good["interval_days"] < better["interval_days"]
    assert 0 < again["interval_days"] < 1
    assert again["lapses"] == 1 and again["review_count"] == 3
    assert schedule({}, "easy", now)["due_at"] > good["due_at"]
    hard = schedule(better, "hard", now)
    assert (
        schedule(hard, "good", now)["interval_days"] >= schedule(hard, "hard", now)["interval_days"]
    )


def test_malformed_files_are_validation_errors(bank):
    _, _, client = bank
    for filename, data in [
        ("broken.xlsx", b"not a workbook"),
        ("broken.json", b"{"),
        ("old.xls", b"old format"),
    ]:
        response = client.post("/practice/import/preview", files={"file": (filename, data)})
        assert response.status_code == 422


def test_import_receipts_and_questions_are_isolated_between_stores(bank, tmp_path):
    _, repo, _ = bank
    token = repo.stage_import(
        "q.json", "bank", [normalize_question({"question": "Q", "answer": "A"})]
    )
    other = SQLiteSessionStore(tmp_path / "other-user.db")
    other_repo = PracticeStore(other.db_path)
    with pytest.raises(LookupError):
        other_repo.commit_import(token)
    repo.commit_import(token)
    assert other_repo.overview("UTC")["total"] == 0
    with pytest.raises(LookupError):
        other_repo.review(1, "other-user-attempt", 0, "again", "")


def test_imports_keep_file_provenance_without_creating_sessions(bank):
    store, repo, client = bank
    question = normalize_question({"question": "Q", "answer": "A"})
    repo.commit_import(repo.stage_import("lesson.csv", "mistakes", [question]))
    listing = asyncio.run(store.list_notebook_entries())
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["session_id"] == ""
    assert item["origin_type"] == "external_import"
    assert item["origin_ref"] == "practice-import"
    assert item["material_id"].startswith("import:")
    assert repo.overview("UTC")["mistakes"] == 1
    assert asyncio.run(store.list_sessions()) == []
    assert client.get("/bank/entries").json()["total"] == 1
    assert client.get("/practice/summary").json()["mistakes"] == 1


def test_analytics_separates_creation_first_mistake_and_repeat_reviews(bank):
    store, repo, client = bank
    now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc).timestamp()
    entry = seed(store, source="mastery_path", result="ungraded")
    with repo.connect() as conn:
        conn.execute(
            "UPDATE notebook_entries SET created_at=? WHERE id=?", (now - 3 * DAY, entry["id"])
        )
    repo.review(entry["id"], "first-wrong", 0, "again", "A", now=now - DAY)
    repo.review(entry["id"], "second-wrong", 1, "again", "A", now=now)
    repo.review(entry["id"], "second-wrong", 1, "again", "A", now=now)
    report = repo.analytics("Asia/Shanghai", 7, now=now)
    assert report["totals"] == {"questions": 1, "mistakes": 1, "reviews": 2}
    assert len(report["daily"]) == 7
    assert report["daily"][-4] == {
        "date": "2026-09-16",
        "questions": 1,
        "mistakes": 0,
        "reviews": 0,
    }
    assert report["daily"][-2]["mistakes"] == 1
    assert report["daily"][-1]["mistakes"] == 0
    assert report["sources"] == [{"source": "mastery_path", **report["totals"]}]
    asyncio.run(store.update_notebook_entry(entry["id"], {"resolved": True}))
    assert repo.analytics("Asia/Shanghai", 7, now=now)["totals"] == report["totals"]
    assert client.get("/practice/analytics?days=8").status_code == 422
    assert client.get("/practice/analytics?timezone=not-a-zone").status_code == 422


def test_analytics_local_midnight_dst_zero_days_and_deleted_sources(bank):
    store, repo, _ = bank
    now = datetime(2026, 3, 10, 12, tzinfo=timezone.utc).timestamp()
    # These straddle local midnight during the daylight-saving transition.
    entries = [seed(store, question_id=f"dst-{i}", source="book") for i in range(2)]
    timestamps = [
        datetime(2026, 3, 8, 4, 59, tzinfo=timezone.utc).timestamp(),
        datetime(2026, 3, 8, 5, 1, tzinfo=timezone.utc).timestamp(),
    ]
    with repo.connect() as conn:
        for entry, ts in zip(entries, timestamps, strict=True):
            conn.execute("UPDATE notebook_entries SET created_at=? WHERE id=?", (ts, entry["id"]))
            conn.execute(
                "UPDATE practice_review_state SET first_wrong_at=? WHERE entry_id=?",
                (ts, entry["id"]),
            )
    data = repo.analytics("America/New_York", 7, now=now)
    by_day = {row["date"]: row for row in data["daily"]}
    assert by_day["2026-03-07"]["questions"] == by_day["2026-03-08"]["questions"] == 1
    assert by_day["2026-03-09"]["questions"] == 0
    with repo.connect() as conn:
        conn.execute("UPDATE sessions SET deleted_at=? WHERE id='lesson'", (now,))
    assert repo.analytics("America/New_York", 7, now=now)["sources"] == []


def _figure_png() -> bytes:
    image = pytest.importorskip("PIL.Image")
    draw = pytest.importorskip("PIL.ImageDraw")
    canvas = image.new("RGB", (320, 160), "white")
    pen = draw.Draw(canvas)
    pen.rectangle([10, 10, 310, 150], outline="black", width=3)
    pen.line([(10, 80), (310, 80)], fill="black", width=2)
    output = io.BytesIO()
    canvas.save(output, "PNG")
    return output.getvalue()


class _FakeAttachmentStore:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, bytes]] = []

    async def put(
        self,
        *,
        session_id: str,
        attachment_id: str,
        filename: str,
        data: bytes,
        mime_type: str = "",
    ) -> str:
        self.uploads.append((filename, mime_type, data))
        return f"/files/attachments/{session_id}/{attachment_id}/{filename}"


def test_xlsx_embedded_figure_survives_import(bank, monkeypatch):
    store, repo, client = bank
    from openpyxl.drawing.image import Image as XLImage

    fake = _FakeAttachmentStore()
    monkeypatch.setattr("deeptutor.services.storage.get_attachment_store", lambda: fake)
    book = Workbook()
    sheet = book.active
    sheet.append(["question", "question_type", "A", "B", "C", "D", "correct_answer", "explanation", "tags", "user_answer"])
    sheet.append(["如图，重叠后的总长是多少？", "single_choice", "140", "130", "120", "110", "B", "", "数学", ""])
    sheet.add_image(XLImage(io.BytesIO(_figure_png())), "A2")
    output = io.BytesIO()
    book.save(output)
    book.close()

    result = client.post(
        "/practice/import/preview",
        files={"file": ("figures.xlsx", output.getvalue())},
        data={"target": "mistakes"},
    ).json()
    assert result["valid"] == 1 and result["token"]
    assert result["samples"][0]["question_images"][0]["url"].startswith("/files/attachments/")
    assert fake.uploads and fake.uploads[0][0] == "image-01.png"

    assert client.post("/practice/import/commit", json={"token": result["token"]}).json()["created"] == 1
    entry = asyncio.run(store.list_notebook_entries())["items"][0]
    assert entry["question_images"][0]["filename"] == "image-01.png"
    assert entry["question_images"][0]["url"].startswith("/files/attachments/")


def test_image_batch_imports_as_one_group(bank, monkeypatch):
    store, _repo, client = bank
    from deeptutor.services.practice import document_import

    fake = _FakeAttachmentStore()
    monkeypatch.setattr("deeptutor.services.storage.get_attachment_store", lambda: fake)

    class _VisionClient:
        config = type("_Config", (), {"model": "vision-model", "binding": "openai"})()

        def supports_multimodal_images(self) -> bool:
            return True

    async def _fake_vision(image, *, mime_type="image/png", filename="page.png"):
        return [
            {
                "question": f"{Path(filename).stem} 里的题目",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "答案",
                "explanation": "",
                "tags": [],
                "user_answer": "",
                "figure_boxes": [],
            }
        ]

    monkeypatch.setattr(document_import, "_llm_client", lambda: _VisionClient())
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: True)
    monkeypatch.setattr(document_import, "_ask_vision_model", _fake_vision)

    png = _figure_png()
    response = client.post(
        "/practice/import/preview",
        files=[
            ("files", ("page-a.png", png, "image/png")),
            ("files", ("page-b.png", png, "image/png")),
        ],
        data={"target": "bank", "label": "错题本照片"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["valid"] == 2 and payload["token"]
    assert (
        client.post("/practice/import/commit", json={"token": payload["token"]}).json()["created"]
        == 2
    )

    rows = asyncio.run(store.list_notebook_navigation())
    assert len(rows) == 1
    assert rows[0]["title"] == "错题本照片"
    assert rows[0]["count"] == 2
    assert rows[0]["material_id"].startswith("import:")
    assert rows[0]["group_ref"] == f"g:material|{rows[0]['material_id']}"
    entries = asyncio.run(store.list_notebook_entries())["items"]
    assert {entry["material_title"] for entry in entries} == {"错题本照片"}


def test_large_uploaded_photos_are_compressed_before_the_model_and_storage(bank, monkeypatch):
    store, _repo, client = bank
    from deeptutor.services.practice import document_import

    fake = _FakeAttachmentStore()
    monkeypatch.setattr("deeptutor.services.storage.get_attachment_store", lambda: fake)
    seen: list[int] = []

    class _VisionClient:
        config = type("_Config", (), {"model": "vision-model", "binding": "openai"})()

        def supports_multimodal_images(self) -> bool:
            return True

    async def _fake_vision(image, *, mime_type="image/png", filename="page.png"):
        seen.append(len(image))
        return [
            {
                "question": "照片里的题目",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "答案",
                "explanation": "",
                "tags": [],
                "user_answer": "",
                "figure_boxes": [],
            }
        ]

    monkeypatch.setattr(document_import, "_llm_client", lambda: _VisionClient())
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: True)
    monkeypatch.setattr(document_import, "_ask_vision_model", _fake_vision)

    from PIL import Image as PILImage

    photo = PILImage.effect_noise((2200, 1400), 64).convert("RGB")
    buffer = io.BytesIO()
    photo.save(buffer, format="JPEG", quality=95)
    original = buffer.getvalue()

    response = client.post(
        "/practice/import/preview",
        files={"file": ("photo.jpg", original, "image/jpeg")},
        data={"target": "bank"},
    )
    assert response.status_code == 200, response.text
    assert seen and seen[0] < len(original)
    stored_name, stored_mime, stored = fake.uploads[0]
    assert len(stored) < len(original)
    assert stored_mime in {"image/webp", "image/jpeg"}
    assert Path(stored_name).suffix in {".webp", ".jpg"}


def test_multiple_uploads_that_are_not_all_images_are_rejected(bank):
    _store, _repo, client = bank
    response = client.post(
        "/practice/import/preview",
        files=[
            ("files", ("page-a.png", _figure_png(), "image/png")),
            ("files", ("paper.pdf", b"%PDF-1.4", "application/pdf")),
        ],
        data={"target": "bank"},
    )
    assert response.status_code == 422
    assert "must be images" in response.json()["detail"]


def test_image_import_without_a_vision_model_is_rejected(bank, monkeypatch):
    _store, _repo, client = bank
    from deeptutor.services.practice import document_import

    class _TextClient:
        config = type("_Config", (), {"model": "deepseek-chat", "binding": "deepseek"})()

        def supports_multimodal_images(self) -> bool:
            return False

    monkeypatch.setattr(document_import, "_llm_client", lambda: _TextClient())
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: None)

    response = client.post(
        "/practice/import/preview",
        files=[("files", ("page-a.png", _figure_png(), "image/png"))],
        data={"target": "bank"},
    )
    assert response.status_code == 422
    assert "deepseek-chat" in response.json()["detail"]


def test_pdf_and_docx_import_keep_inline_figures(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    docx = pytest.importorskip("docx")
    from docx.shared import Inches

    from deeptutor.services.practice.document_import import preview_document

    png = _figure_png()

    pdf = pymupdf.open()
    page = pdf.new_page()
    y = 72
    for line in [
        "1. 计算 2+2=？",
        "A. 3",
        "B. 4",
        "C. 5",
        "D. 6",
        "答案：B",
        "解析：2+2=4。",
    ]:
        page.insert_text((72, y), line, fontname="china-s", fontsize=12)
        y += 20
    y += 10
    for line in [
        "2. 两根木条重叠后如图，总长是多少？",
        "A. 140",
        "B. 130",
        "C. 120",
        "D. 110",
        "答案：B",
        "解析：60+80-10=130。",
    ]:
        page.insert_text((72, y), line, fontname="china-s", fontsize=12)
        y += 20
    page.insert_image(pymupdf.Rect(400, 250, 560, 330), stream=png)
    pdf_bytes = pdf.tobytes()
    pdf.close()

    document = docx.Document()
    document.add_paragraph("1. 计算 2+2=？")
    for line in ["A. 3", "B. 4", "C. 5", "D. 6", "答案：B", "解析：2+2=4。"]:
        document.add_paragraph(line)
    document.add_paragraph("2. 如图，重叠后的总长是多少？")
    document.add_picture(io.BytesIO(png), width=Inches(2.2))
    for line in ["A. 140", "B. 130", "C. 120", "D. 110", "答案：B", "解析：60+80-10=130。"]:
        document.add_paragraph(line)
    docx_path = tmp_path / "figure.docx"
    document.save(docx_path)

    pdf_rows = asyncio.run(preview_document(pdf_bytes, "figure.pdf"))
    docx_rows = asyncio.run(preview_document(docx_path.read_bytes(), "figure.docx"))
    for parsed in (pdf_rows, docx_rows):
        assert not parsed["errors"]
        assert len(parsed["questions"]) == 2
        assert len(parsed["questions"][0]["question_images"]) == 0
        assert len(parsed["questions"][1]["question_images"]) == 1
        assert parsed["questions"][1]["correct_answer"] == "B"


def _image_only_pdf() -> bytes:
    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    # A whole-page image is exactly how a scan arrives: the header/footer
    # filter drops it, so image flags alone cannot identify a scanned PDF.
    page.insert_image(page.rect, stream=_figure_png())
    data = document.tobytes()
    document.close()
    return data


def test_image_only_pdf_reaches_the_vision_path(monkeypatch):
    from deeptutor.services.practice import document_import

    class _VisionClient:
        config = type("_Config", (), {"model": "vision-model", "binding": "openai"})()

        def supports_multimodal_images(self) -> bool:
            return True

    pages: list[bytes] = []

    async def _fake_vision(
        page_png: bytes, *, mime_type: str = "image/png", filename: str = "page.png"
    ) -> list[dict]:
        pages.append(page_png)
        return [
            {
                "question": "如图，求阴影面积。",
                "question_type": "single_choice",
                "options": {"A": "10", "B": "20", "C": "30", "D": "40"},
                "correct_answer": "B",
                "explanation": "",
                "tags": [],
                "user_answer": "",
                "figure_boxes": [],
            }
        ]

    monkeypatch.setattr(document_import, "_llm_client", lambda: _VisionClient())
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: None)
    monkeypatch.setattr(document_import, "_ask_vision_model", _fake_vision)

    parsed = asyncio.run(document_import.preview_document(_image_only_pdf(), "scan.pdf"))
    assert len(pages) == 1
    assert parsed["questions"][0]["correct_answer"] == "B"


def test_image_only_pdf_without_vision_names_the_active_model(monkeypatch):
    from deeptutor.services.practice import document_import

    class _TextClient:
        config = type("_Config", (), {"model": "deepseek-chat", "binding": "deepseek"})()

        def supports_multimodal_images(self) -> bool:
            return False

    monkeypatch.setattr(document_import, "_llm_client", lambda: _TextClient())
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: None)

    with pytest.raises(document_import.DocumentImportError) as raised:
        asyncio.run(document_import.preview_document(_image_only_pdf(), "scan.pdf"))
    message = str(raised.value)
    assert "deepseek-chat" in message and "vision-capable" in message


def test_declared_vision_overrides_the_capability_table(monkeypatch):
    from deeptutor.services.practice import document_import

    class _TextClient:
        config = type("_Config", (), {"model": "custom-vl", "binding": "deepseek"})()

        def supports_multimodal_images(self) -> bool:
            return False

    monkeypatch.setattr(document_import, "_active_model_vision", lambda: True)
    assert document_import._client_supports_vision(_TextClient()) is True


def test_sparse_text_pdf_with_a_large_figure_stays_on_the_text_path(monkeypatch):
    from deeptutor.services.practice import document_import

    pymupdf = pytest.importorskip("pymupdf")
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(
        pymupdf.Rect(20, 20, page.rect.width - 20, page.rect.height - 20),
        stream=_figure_png(),
    )
    for index, line in enumerate(["1. 计算 2+2=？", "A. 3", "B. 4", "C. 5", "D. 6", "答案：B"]):
        page.insert_text((72, 300 + index * 20), line, fontname="china-s", fontsize=12)
    data = document.tobytes()
    document.close()

    # The text is sparse enough for the scan heuristic, but it does parse, so
    # the importer must not send a text-based PDF to a vision model.
    async def _unexpected(_page_png: bytes) -> list[dict]:
        raise AssertionError("vision path used for a text PDF")

    monkeypatch.setattr(document_import, "_ask_vision_model", _unexpected)
    parsed = asyncio.run(document_import.preview_document(data, "sparse.pdf"))
    assert len(parsed["questions"]) == 1
    assert parsed["questions"][0]["correct_answer"] == "B"


def test_figure_verification_can_rasterize_a_redrawn_svg():
    from deeptutor.services.practice.document_import import _box_contains, _svg_to_png

    pytest.importorskip("pymupdf")
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect x="10" y="10" width="180" height="80" fill="none" stroke="black"/>'
        '<text x="30" y="60" font-size="16">10cm</text></svg>'
    )
    png = _svg_to_png(svg)
    assert png is not None and png.startswith(b"\x89PNG")
    assert _svg_to_png("<script>alert(1)</script>") is None
    assert _box_contains((0.1, 0.1, 0.9, 0.9), (0.2, 0.2, 0.8, 0.8))
    assert not _box_contains((0.2, 0.2, 0.8, 0.8), (0.1, 0.1, 0.9, 0.9))


def test_duplicate_import_refreshes_question_images(bank):
    store, repo, _ = bank
    question = normalize_question(
        {"question": "如图，求长度", "A": "8", "B": "6", "answer": "B", "tags": "数学"}
    )
    old = {"id": "old", "url": "/files/old.png", "filename": "old.png", "mime_type": "image/png"}
    new = {"id": "new", "url": "/files/new.png", "filename": "new.png", "mime_type": "image/png"}
    first = dict(question, question_images=[old])
    second = dict(question, question_images=[new])
    assert repo.commit_import(repo.stage_import("q.json", "bank", [first]))["created"] == 1
    again = repo.commit_import(repo.stage_import("q.json", "bank", [second]))
    assert again["duplicates"] == 1
    entry = asyncio.run(store.list_notebook_entries())["items"][0]
    assert entry["question_images"] == [new]


def test_navigation_aggregates_practice_questions_by_import_file(bank):
    store, repo, _ = bank
    first = [
        normalize_question({"question": f"file one q{i}", "answer": "A"}) for i in range(3)
    ]
    second = [normalize_question({"question": "file two q", "answer": "B"})]
    repo.commit_import(repo.stage_import("one.pdf", "bank", first))
    repo.commit_import(repo.stage_import("two.pdf", "bank", second))
    seed(store, question_id="loose", question="loose question")

    rows = asyncio.run(store.list_notebook_navigation())
    by_title = {row["title"]: row for row in rows}
    assert by_title["one.pdf"]["count"] == 3
    assert by_title["one.pdf"]["material_id"].startswith("import:")
    assert by_title["two.pdf"]["count"] == 1
    # A conversation's questions are a set too: named after the conversation
    # and addressed by its session + turn, not by that name.
    assert by_title["Lesson"]["count"] == 1
    assert by_title["Lesson"]["material_id"] == ""
    assert by_title["Lesson"]["group_ref"].startswith("g:turn|")
    assert len(rows) == 3


def test_navigation_refs_open_exactly_the_entries_they_count(bank):
    """A sidebar row is a promise: "3 questions" must open those 3."""
    store, repo, _ = bank
    repo.commit_import(
        repo.stage_import(
            "fractions.csv",
            "bank",
            [normalize_question({"question": f"fractions q{i}", "answer": "A"}) for i in range(3)],
        )
    )
    repo.commit_import(
        repo.stage_import(
            "geometry.csv",
            "bank",
            [normalize_question({"question": "geometry q", "answer": "B"})],
        )
    )
    seed(store, question_id="loose", question="loose question")

    for row in asyncio.run(store.list_notebook_navigation()):
        listing = asyncio.run(
            store.list_notebook_entries(group_ref=row["group_ref"], limit=200)
        )
        assert listing["total"] == row["count"], row["group_ref"]
        ids = [item["id"] for item in listing["items"]]
        assert row["id"] in ids


def _vision_rows(rows):
    """Stand-in for the model reply, so the pipeline runs without a network."""

    async def reply(image, *, mime_type="image/png", filename="page.png"):
        return rows

    return reply


def _vision_client():
    class _Client:
        config = type("_Config", (), {"model": "vision-model", "binding": "openai"})()

        def supports_multimodal_images(self) -> bool:
            return True

    return _Client()


def _patch_vision(monkeypatch, rows, verdicts=None):
    from deeptutor.services.practice import document_import

    async def no_verdicts(*args, **kwargs):
        return list(verdicts or [])

    monkeypatch.setattr(document_import, "_llm_client", _vision_client)
    monkeypatch.setattr(document_import, "_active_model_vision", lambda: True)
    monkeypatch.setattr(document_import, "_ask_vision_model", _vision_rows(rows))
    monkeypatch.setattr(document_import, "_check_answers", no_verdicts)
    monkeypatch.setattr(
        "deeptutor.services.storage.get_attachment_store", lambda: _FakeAttachmentStore()
    )


def test_commit_imports_only_the_selected_questions(bank):
    _store, _repo, client = bank
    data = "题目,题型,A,B,答案\n一?,单选题,1,2,A\n二?,单选题,1,2,B\n".encode()
    preview = client.post(
        "/practice/import/preview", files={"file": ("two.csv", data)}, data={"target": "bank"}
    ).json()
    assert preview["valid"] == 2 and len(preview["questions"]) == 2
    commit = client.post(
        "/practice/import/commit", json={"token": preview["token"], "selected": [1]}
    )
    assert commit.json() == {"created": 1, "duplicates": 0, "total": 1, "mistakes": 0}
    items = client.get("/bank/entries").json()["items"]
    assert [item["question"] for item in items] == ["二?"]


def test_an_empty_or_out_of_range_selection_is_rejected(bank):
    _store, _repo, client = bank
    data = "题目,题型,A,B,答案\n一?,单选题,1,2,A\n".encode()
    preview = client.post(
        "/practice/import/preview", files={"file": ("one.csv", data)}, data={"target": "bank"}
    ).json()
    token = preview["token"]
    empty = client.post("/practice/import/commit", json={"token": token, "selected": []})
    assert empty.status_code == 422 and "at least one" in empty.json()["detail"]
    outside = client.post("/practice/import/commit", json={"token": token, "selected": [3]})
    assert outside.status_code == 422
    # Neither rejection consumed the preview: the real selection still lands.
    assert client.post(
        "/practice/import/commit", json={"token": token, "selected": [0]}
    ).json()["created"] == 1


def test_a_question_the_learner_answered_right_is_not_a_mistake(bank, monkeypatch):
    """The folder is a preference; a demonstrated correct answer is evidence."""
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "2+2=?",
                "question_type": "single_choice",
                "options": {"A": "3", "B": "4"},
                "correct_answer": "B",
                "user_answer": "B",
                "explanation": "2+2=4",
                "tags": [],
                "figure_boxes": [],
                "answer_source": "printed",
            }
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    assert preview["questions"][0]["attempt"] == "correct"
    commit = client.post("/practice/import/commit", json={"token": preview["token"]}).json()
    assert commit["mistakes"] == 0
    entry = client.get("/bank/entries").json()["items"][0]
    assert entry["result"] == "correct" and entry["is_correct"] is True
    assert client.get("/bank/stats").json()["wrong"] == 0
    assert client.get("/bank/entries?mistakes_only=true").json()["total"] == 0


def test_a_filled_blank_is_restored_before_it_reaches_the_bank(bank, monkeypatch):
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "秋天是 liáng shuǎng（凉爽）的。",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "凉爽",
                "user_answer": "凉爽",
                "explanation": "写出词语。",
                "tags": [],
                "figure_boxes": [],
            }
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    row = preview["questions"][0]
    assert row["leaks"] == ["凉爽"]
    assert "凉爽" not in row["question"] and "（　　）" in row["question"]
    client.post("/practice/import/commit", json={"token": preview["token"]})
    entry = client.get("/bank/entries").json()["items"][0]
    assert "凉爽" not in entry["question"]


def test_a_disagreeing_second_pass_is_flagged_not_silently_replaced(bank, monkeypatch):
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "数字1的对立面是（ ）",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "3",
                "user_answer": "",
                "explanation": "",
                "tags": [],
                "figure_boxes": [[0.1, 0.1, 0.4, 0.4]],
                "answer_source": "solved",
            }
        ],
        verdicts=[{"index": 0, "answer": "6", "agrees": False, "reason": "different net"}],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    row = preview["questions"][0]
    assert row["answer_conflict"] is True
    assert row["verdict_answer"] == "6"
    assert row["correct_answer"] == "3"  # two guesses are not evidence


def test_a_wrong_solved_answer_is_flagged_by_arithmetic(bank, monkeypatch):
    """Arithmetic outranks the second model pass: 25+14-6 is 33, not 31."""
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "用“○”画出先算什么，再计算。$25+14-6$",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "31",
                "user_answer": "",
                "explanation": "",
                "tags": [],
                "figure_boxes": [],
            }
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "bank"},
    ).json()
    row = preview["questions"][0]
    assert row["answer_conflict"] is True
    assert row["verdict_answer"] == "33"
    assert "25+14-6" in row["verification"]


def test_a_cube_net_answer_is_checked_against_the_folded_net(bank, monkeypatch):
    """The model reports the grid; the solver folds it and decides the answer."""
    net = [[0, 1, "1"], [1, 0, "2"], [1, 1, "4"], [1, 2, "5"], [1, 3, "6"], [2, 1, "3"]]
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "数字1的对立面是（　　）；数字2的对立面是（　　）；数字3的对立面是（　　）",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "3；6；5",
                "user_answer": "",
                "explanation": "",
                "tags": [],
                "figure_boxes": [[0.1, 0.1, 0.5, 0.5]],
                "figure_data": {"kind": "net", "cells": net},
            }
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    row = preview["questions"][0]
    assert row["answer_conflict"] is True
    assert row["verdict_answer"] == "3; 5; 1"


def test_the_same_question_on_one_figure_is_merged(bank, monkeypatch):
    row = {
        "question": "数字1的对立面是（　　）",
        "question_type": "short_answer",
        "options": {},
        "correct_answer": "3",
        "user_answer": "",
        "explanation": "",
        "tags": [],
        "figure_boxes": [[0.1, 0.1, 0.5, 0.5]],
    }
    _patch_vision(monkeypatch, [dict(row), dict(row)])
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    assert len(preview["questions"]) == 1
    assert preview["duplicates_merged"] == 1


def test_the_same_stem_on_different_figures_stays_two_questions(bank, monkeypatch):
    """A sheet of nets prints one identical stem per figure; that is not a repeat."""
    base = {
        "question": "数字1的对立面是（　　）",
        "question_type": "short_answer",
        "options": {},
        "correct_answer": "3",
        "user_answer": "",
        "explanation": "",
        "tags": [],
    }
    _patch_vision(
        monkeypatch,
        [
            {**base, "figure_boxes": [[0.05, 0.05, 0.3, 0.3]]},
            {**base, "figure_boxes": [[0.35, 0.05, 0.6, 0.3]]},
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    assert len(preview["questions"]) == 2
    assert preview["duplicates_merged"] == 0


def test_an_import_job_reports_progress_and_finishes(bank, monkeypatch):
    """A long import must be pollable: the browser needs a number, not a spinner."""
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "秋天是 liáng shuǎng（凉爽）的。",
                "question_type": "short_answer",
                "options": {},
                "correct_answer": "凉爽",
                "user_answer": "凉爽",
                "explanation": "",
                "tags": [],
                "figure_boxes": [],
            }
        ],
    )
    _store, _repo, client = bank
    started = client.post(
        "/practice/import/jobs",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "bank"},
    )
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    job: dict = {}
    for _ in range(200):
        job = client.get(f"/practice/import/jobs/{job_id}").json()
        if job["status"] in {"done", "failed"}:
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    assert job["progress"] == job["total"] == 1
    assert job["preview"]["valid"] == 1 and job["preview"]["token"]
    # The staged preview is the same one the synchronous endpoint would return.
    commit = client.post(
        "/practice/import/commit",
        json={"token": job["preview"]["token"], "selected": [0]},
    ).json()
    assert commit["created"] == 1


def test_an_unknown_import_job_is_a_404(bank):
    _store, _repo, client = bank
    assert client.get("/practice/import/jobs/nope").status_code == 404


def test_an_imported_mistake_is_due_today(bank):
    """A batch the learner just filed is for today, not for tomorrow."""
    store, repo, client = bank
    data = "题目,题型,A,B,答案\n1+1=?,单选题,1,2,B\n".encode()
    preview = client.post(
        "/practice/import/preview", files={"file": ("m.csv", data)}, data={"target": "mistakes"}
    ).json()
    client.post("/practice/import/commit", json={"token": preview["token"]})
    summary = client.get("/practice/summary?timezone=UTC").json()
    assert summary["due"] == 1 and summary["mistakes"] == 1
    entries = client.get("/practice/queue?timezone=UTC").json()
    assert [row["entry"]["question"] for row in entries] == ["1+1=?"]


def test_an_imported_correct_answer_is_due_today_but_is_not_a_mistake(bank, monkeypatch):
    """The folder decides the schedule; the evidence decides the badge."""
    _patch_vision(
        monkeypatch,
        [
            {
                "question": "2+2=?",
                "question_type": "single_choice",
                "options": {"A": "3", "B": "4"},
                "correct_answer": "B",
                "user_answer": "B",
                "explanation": "2+2=4",
                "tags": [],
                "figure_boxes": [],
                "answer_source": "printed",
            }
        ],
    )
    _store, _repo, client = bank
    preview = client.post(
        "/practice/import/preview",
        files=[("files", ("page.png", _figure_png(), "image/png"))],
        data={"target": "mistakes"},
    ).json()
    client.post("/practice/import/commit", json={"token": preview["token"]})
    summary = client.get("/practice/summary?timezone=UTC").json()
    assert summary["due"] == 1 and summary["mistakes"] == 0
