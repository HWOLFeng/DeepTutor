"""Bounded, non-executing parsers for the learner's CSV, XLSX and JSON files."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 500
MAX_CELL = 20000
MAX_COLUMNS = 64
# Question figures are stored once in the AttachmentStore; the import only
# carries them from the parser to the request handler.
MAX_IMAGES_PER_QUESTION = 8
MAX_TOTAL_IMAGES = 200
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
ALIASES = {
    "question": ("question", "题目", "题干"),
    "question_type": ("question_type", "type", "题型"),
    "correct_answer": ("correct_answer", "answer", "答案", "正确答案"),
    "explanation": ("explanation", "解析"),
    "difficulty": ("difficulty", "难度"),
    "tags": ("tags", "标签", "分类"),
    "user_answer": ("user_answer", "我的答案", "作答"),
    "options": ("options", "选项"),
    "question_images": ("question_images", "images", "image", "图片", "题图", "插图"),
}
TYPES = {
    "single_choice": "single_choice",
    "multiple_choice": "single_choice",
    "mcq": "single_choice",
    "单选": "single_choice",
    "单选题": "single_choice",
    "multi_choice": "multi_choice",
    "多选": "multi_choice",
    "多选题": "multi_choice",
    "true_false": "true_false",
    "判断": "true_false",
    "判断题": "true_false",
    "fill_blank": "fill_blank",
    "填空": "fill_blank",
    "填空题": "fill_blank",
    "short_answer": "short_answer",
    "简答": "short_answer",
    "简答题": "short_answer",
    "essay": "short_answer",
    "free_response": "short_answer",
}


def _cell(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > MAX_CELL:
        raise ValueError("A cell exceeds 20,000 characters")
    return text


def _image_record(
    data: bytes,
    filename: str,
    mime_type: str = "",
    *,
    total: list[int] | None = None,
) -> dict:
    """Validate one image payload against the per-file import budgets."""
    if not data:
        raise ValueError("An attached image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("An attached image exceeds 4 MB")
    if total is not None:
        if total[0] >= MAX_TOTAL_IMAGES or total[1] + len(data) > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("Too many or too large images in one import")
        total[0] += 1
        total[1] += len(data)
    suffix = Path(filename).suffix.lower()
    if not mime_type:
        mime_type = IMAGE_MIME_BY_EXT.get(suffix, "image/png")
    if not mime_type.startswith("image/"):
        raise ValueError("Only raster image attachments are supported")
    return {
        "id": "",
        "url": "",
        "filename": (filename or "figure.png")[:120],
        "mime_type": mime_type,
        "data": data,
    }


def _data_uri_image(value: str, *, total: list[int] | None = None) -> dict | None:
    match = re.match(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$", value.strip(), re.I | re.S)
    if not match:
        return None
    mime_type, encoded = match.group(1).lower(), match.group(2)
    try:
        data = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image data") from exc
    ext = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(mime_type, ".png")
    return _image_record(data, f"figure{ext}", mime_type, total=total)


def _collect_images(raw_value: object, embedded: list[dict] | None = None) -> list[dict]:
    """Normalize images from a cell/JSON field plus xlsx embedded pictures.

    External ``http(s)`` URLs are deliberately refused: the server must not
    fetch arbitrary hosts on behalf of an uploaded file. Data URIs and
    workbook-embedded pictures are accepted.
    """
    records: list[dict] = list(embedded or [])
    total = [0, 0]
    for record in records:
        _image_record(
            record.get("data") or b"",
            str(record.get("filename") or "figure.png"),
            str(record.get("mime_type") or ""),
            total=total,
        )
    if raw_value in (None, ""):
        return records
    value = raw_value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return records
        if text.startswith("data:"):
            image = _data_uri_image(text, total=total)
            return [*records, image] if image else records
        if text.startswith(("http://", "https://")):
            raise ValueError(
                "External image URLs are not fetched; embed the picture in the file "
                "or use a base64 data URI"
            )
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ValueError("The image column must hold a data URI or image objects") from exc
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        raise ValueError("The image column must hold a data URI or a list of image objects")
    for item in value:
        if isinstance(item, str):
            image = _data_uri_image(item, total=total)
            if image is None:
                raise ValueError("Image entries must be data URIs")
            records.append(image)
            continue
        if not isinstance(item, dict):
            raise ValueError("Image entries must be data URIs or image objects")
        data = item.get("data") or item.get("base64") or item.get("data_uri") or ""
        if isinstance(data, str):
            if data.startswith("data:"):
                image = _data_uri_image(data, total=total)
                if image is None:
                    raise ValueError("Invalid image data URI")
                records.append(image)
                continue
            try:
                data = base64.b64decode(data, validate=False)
            except (ValueError, TypeError) as exc:
                raise ValueError("Invalid base64 image data") from exc
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("Image objects must carry raw or base64 image data")
        records.append(
            _image_record(
                bytes(data),
                str(item.get("filename") or "figure.png"),
                str(item.get("mime_type") or ""),
                total=total,
            )
        )
    return records[:MAX_IMAGES_PER_QUESTION]


def _xlsx_embedded_images(data: bytes) -> dict[int, list[dict]]:
    """Embedded pictures keyed by the 1-based Excel row that anchors them."""
    try:
        from openpyxl import load_workbook

        from deeptutor.utils.document_images import ImageBudget
    except ImportError:  # pragma: no cover - openpyxl is a hard dependency
        return {}
    try:
        book = load_workbook(io.BytesIO(data), data_only=False, keep_links=False)
    except Exception:
        return {}
    found: dict[int, list[dict]] = {}
    budget = ImageBudget(
        max_images=MAX_TOTAL_IMAGES,
        max_total_bytes=MAX_TOTAL_IMAGE_BYTES,
        max_image_bytes=MAX_IMAGE_BYTES,
        min_image_bytes=256,
        max_inline_edge=0,
    )
    try:
        sheet = book.active
        if sheet is None:
            return {}
        for picture in getattr(sheet, "_images", []) or []:
            anchor = getattr(picture, "anchor", None)
            start = getattr(anchor, "_from", None)
            row = int(getattr(start, "row", -1)) + 1 if start is not None else 0
            if row < 2:
                continue
            try:
                blob = picture._data()  # openpyxl Image; bytes of the source file
            except Exception:
                continue
            fmt = str(getattr(picture, "format", "") or "png").lower()
            ext = ".jpg" if fmt in {"jpg", "jpeg"} else f".{fmt}"
            admitted = budget.try_admit(ext, bytes(blob))
            if admitted is None:
                continue
            found.setdefault(row, []).append(
                {
                    "id": "",
                    "url": "",
                    "filename": admitted.name,
                    "mime_type": admitted.mime_type,
                    "data": admitted.data,
                }
            )
    finally:
        book.close()
    return found


def parse_rows(data: bytes, filename: str) -> list[tuple[int, dict]]:
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Choose a non-empty file up to 5 MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".json":
        rows = json.loads(data.decode("utf-8-sig"))
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError("JSON must contain an array of question objects")
        if len(rows) > MAX_ROWS:
            raise ValueError("Import at most 500 questions at a time")
        return list(enumerate(rows, 1))
    if suffix in {".csv", ".tsv"}:
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = data.decode("gb18030")
        try:
            dialect = csv.Sniffer().sniff(content[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        rows = []
        try:
            for row in csv.reader(io.StringIO(content, newline=""), dialect):
                if len(row) > MAX_COLUMNS:
                    raise ValueError("Import at most 64 columns at a time")
                rows.append(row)
                if len(rows) > MAX_ROWS + 1:
                    raise ValueError("Import at most 500 questions at a time")
        except csv.Error as exc:
            raise ValueError("Invalid CSV data or a cell exceeds the supported size") from exc
    elif suffix == ".xlsx":
        from openpyxl import load_workbook

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 30 * 1024 * 1024:
                raise ValueError("The expanded workbook exceeds 30 MB")
            if len(archive.infolist()) > 2000:
                raise ValueError("The workbook contains too many parts")
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)
        try:
            sheet = book.active
            if sheet is None:
                raise ValueError("The workbook has no active worksheet")
            if (sheet.max_column or 0) > MAX_COLUMNS:
                raise ValueError("Import at most 64 columns at a time")
            rows = []
            for cells in sheet.iter_rows():
                if len(cells) > MAX_COLUMNS:
                    raise ValueError("Import at most 64 columns at a time")
                if any(cell.data_type == "f" for cell in cells):
                    raise ValueError("Replace spreadsheet formulas with values before importing")
                rows.append([cell.value for cell in cells])
                if len(rows) > MAX_ROWS + 1:
                    raise ValueError("Import at most 500 questions at a time")
        finally:
            book.close()
    else:
        raise ValueError("Supported formats: .xlsx, .csv, .tsv and .json")
    if not rows:
        raise ValueError("The file is empty")
    headers = [_cell(value).lower() for value in rows[0]]
    nonempty = [header for header in headers if header]
    if len(nonempty) != len(set(nonempty)):
        raise ValueError("Column names must be unique")
    if len(rows) > MAX_ROWS + 1:
        raise ValueError("Import at most 500 questions at a time")
    embedded_by_row = _xlsx_embedded_images(data) if suffix == ".xlsx" else {}
    parsed: list[tuple[int, dict]] = []
    for number, row in enumerate(rows[1:], 2):
        if not any(_cell(value) for value in row):
            continue
        record = dict(zip(headers, row, strict=False))
        if number in embedded_by_row:
            record["__images__"] = embedded_by_row[number]
        parsed.append((number, record))
    return parsed


def normalize_question(raw: dict) -> dict:
    row = {str(key).strip().lower(): value for key, value in raw.items()}
    fields = {key: next((row[a] for a in names if a in row), "") for key, names in ALIASES.items()}
    embedded = row.pop("__images__", [])
    question, answer = _cell(fields["question"]), _cell(fields["correct_answer"])
    if not question or not answer:
        raise ValueError("Question and correct answer are required")
    question_images = _collect_images(fields["question_images"], embedded)
    options = fields["options"] or {}
    if isinstance(options, str):
        options = json.loads(options)
    if isinstance(options, list):
        options = {chr(65 + i): value for i, value in enumerate(options)}
    if not isinstance(options, dict) or len(options) > 10:
        raise ValueError("Options must contain at most 10 named choices")
    options = {
        str(key).strip().upper(): _cell(value) for key, value in options.items() if _cell(value)
    }
    for letter in "ABCDEFGHIJ":
        value = row.get(letter.lower(), row.get(f"选项{letter.lower()}", ""))
        if _cell(value):
            options[letter] = _cell(value)
    raw_type = _cell(fields["question_type"]).lower()
    kind = TYPES.get(raw_type) if raw_type else ("single_choice" if options else "short_answer")
    if kind is None:
        raise ValueError("Unknown question type")
    if kind == "true_false":
        truths = {"true", "正确", "对", "是", "t", "1"}
        falses = {"false", "错误", "错", "否", "f", "0"}
        if answer.lower() not in truths | falses:
            raise ValueError("A true/false answer must be true or false")
        options = {"T": "True", "F": "False"}
        answer = "T" if answer.lower() in truths else "F"
    if kind in {"single_choice", "multi_choice"}:
        if len(options) < 2:
            raise ValueError("Choice questions need at least two options")
        if answer in options.values():
            answer = next(key for key, text in options.items() if text == answer)
        keys = [key.strip().upper() for key in re.split(r"[,;，；\s]+", answer) if key.strip()]
        if kind == "multi_choice" and len(keys) == 1 and keys[0] not in options:
            keys = list(keys[0])
        if (
            not keys
            or any(key not in options for key in keys)
            or (kind == "single_choice" and len(keys) != 1)
        ):
            raise ValueError("The answer must name existing option keys (multi-choice: A,C)")
        answer = ",".join(sorted(set(keys)))
    tags_value = fields["tags"]
    tags = tags_value if isinstance(tags_value, list) else re.split(r"[,;，；]", _cell(tags_value))
    tags = list(dict.fromkeys(_cell(tag) for tag in tags if _cell(tag)))
    if len(tags) > 20 or any(len(tag) > 100 for tag in tags):
        raise ValueError("Use at most 20 tags, each up to 100 characters")
    result = dict(
        question=question,
        question_type=kind,
        options=options,
        correct_answer=answer,
        explanation=_cell(fields["explanation"]),
        difficulty=_cell(fields["difficulty"]),
        user_answer=_cell(fields["user_answer"]),
        tags=tags,
        question_images=question_images,
    )
    identity = {
        key: result[key] for key in ("question", "question_type", "options", "correct_answer")
    }
    result["question_id"] = (
        "import:"
        + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
    )
    return result


def preview(data: bytes, filename: str) -> dict:
    questions, errors = [], []
    for number, row in parse_rows(data, filename):
        try:
            questions.append(normalize_question(row))
        except (ValueError, TypeError) as exc:
            errors.append({"row": number, "message": str(exc)})
    if not questions and not errors:
        raise ValueError("The file contains no questions")
    return {"questions": questions, "errors": errors}
