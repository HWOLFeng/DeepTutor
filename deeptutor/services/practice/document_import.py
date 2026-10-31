"""Import question documents (PDF/DOCX) with their figures.

Two extraction paths share one row schema:

* text documents — read the text layer and inline ``[图片 N: name]`` markers,
  split questions with a deterministic parser, and fall back to the configured
  model only when an answer is missing;
* scanned PDFs — render each page and ask a vision-capable model for the rows
  plus normalized bounding boxes for any figure a question references.

The module returns question dicts in the same shape as ``importing.preview``;
the API turns ``question_images[].data`` into AttachmentStore URLs.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path
import re
from typing import Any

from deeptutor.utils.document_images import (
    ImageBudget,
    build_marker,
    extract_docx_rich,
)

from .importing import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_QUESTION,
    MAX_TOTAL_IMAGE_BYTES,
    MAX_TOTAL_IMAGES,
)

# ``importing`` owns the canonical validators; importing the module (not the
# symbols) keeps this table in one place without a circular value import.
from . import importing as _importing

MAX_VISION_PAGES = 20
MAX_TEXT_CHARS = 60_000
MAX_FIGURE_VERIFICATIONS = 20
VISION_DPI = 150

_FIGURE_VERIFY_PROMPT = """You audit one figure crop attached to an exam question.
The image is a composite: LEFT = the full page, RIGHT = the crop currently used.
Return ONLY a JSON object:
{"complete": bool, "reason": str,
 "corrected_boxes": [[x0,y0,x1,y1], ...], "redraw_svg": str|null}
Rules:
- complete=true only when the RIGHT crop contains the entire figure the question
  needs, with no cut-off edges and no unrelated question text, options, page
  headers or captions. A little surrounding whitespace is fine.
- If complete=false and the missing/extra content is recoverable by moving the
  box, return corrected_boxes normalized 0-1 relative to the LEFT page
  (origin top-left), tight around the figure only. The box must overlap the
  current crop and include the whole drawing; never return a box around just a
  dimension label or a fragment.
- If no crop can recover it (vector drawing, cut-off, overlaid text), return a
  faithful self-contained SVG in redraw_svg (no scripts, external images or
  web fonts; include all labels and dimensions as <text>). Use a viewBox that
  matches the figure. For geometry/line diagrams, prefer redraw_svg over a
  fragile coordinate correction.
- Use [] / null when you cannot improve it. Never invent labels or values.
QUESTION:
"""

_SCHEMA_PROMPT = """You convert question papers and mistake notebooks into a JSON array.
Each element: {"question": str,
 "question_type": "single_choice"|"multi_choice"|"true_false"|"short_answer",
 "options": {"A": str, "B": str, "C": str, "D": str},
 "correct_answer": str, "explanation": str, "tags": [str],
 "user_answer": str, "figure_boxes": [[x0,y0,x1,y1], ...]}
Rules:
- Transcribe faithfully in the source language; keep formulas, units and sub-questions.
- Drop page headers, logos, footers, "第N题" headings and the item number ("10.").
- Options hold the content WITHOUT the letter prefix; omit absent options.
- correct_answer: choice letter(s), comma-joined for multi-select, "true"/"false",
  or answer text. Solve it yourself when the source does not print the answer.
- user_answer: when a tick/check mark selects an option, put that letter here.
- explanation: a concise solution. tags: 1-3 short topic labels.
- figure_boxes: normalized [x0,y0,x1,y1] (0-1, origin top-left) around a figure
  that belongs to THIS question; use [] when there is no figure. Never box the
  whole page, the header or a logo.
- Return [] when the page has no questions. Never invent questions.
"""

_MARKER_RE = re.compile(r"\[图片\s*(\d+):\s*([^\]\s]+)\]")
_OPTION_RE = re.compile(r"^\s*([A-D])\s*[.、．:：)]\s*(.+?)\s*$", re.M)
_ANSWER_RE = re.compile(r"^\s*(?:参考)?答案\s*[:：]?\s*(.+?)\s*$", re.M)
_EXPLAIN_RE = re.compile(r"^\s*(?:解析|分析|解答|详解)\s*[:：]?\s*(.+?)\s*$", re.M)
_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:第\s*\d+\s*题[^\n]*|\d{1,3}\s*[.、．]|（\s*\d{1,3}\s*）)\s*"
)


class DocumentImportError(ValueError):
    """A PDF/DOCX cannot be turned into practice rows."""


def _image_record(data: bytes, name: str) -> dict:
    mime = "image/png"
    suffix = Path(name).suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix, mime)
    return {"id": "", "url": "", "filename": name, "mime_type": mime, "data": data}


def _text_from_block(block: dict) -> str:
    lines = []
    for line in block.get("lines") or []:
        text = "".join(span.get("text") or "" for span in line.get("spans") or [])
        if text.strip():
            lines.append(text)
    return "\n".join(lines)


def _extract_pdf_rich(data: bytes) -> tuple[str, dict[str, dict], int, bool]:
    import pymupdf

    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise DocumentImportError(f"The PDF cannot be opened: {exc}") from exc
    budget = ImageBudget(
        max_images=MAX_TOTAL_IMAGES,
        max_total_bytes=MAX_TOTAL_IMAGE_BYTES,
        max_image_bytes=MAX_IMAGE_BYTES,
        min_image_bytes=256,
        max_inline_edge=0,
    )
    images: dict[str, dict] = {}
    pages: list[str] = []
    try:
        if document.is_encrypted and not document.authenticate(""):
            raise DocumentImportError("The PDF is encrypted and cannot be read")
        for page in document:
            items: list[tuple[float, float, str]] = []
            for block in page.get_text("dict").get("blocks") or []:
                bbox = block.get("bbox") or (0, 0, 0, 0)
                if block.get("type") == 0:
                    text = _text_from_block(block)
                    if text.strip():
                        items.append((float(bbox[1]), float(bbox[0]), text))
                    continue
                if block.get("type") != 1:
                    continue
                # Running headers/footers and page-size decorations are not
                # question figures; admitting them would attach the school
                # logo to whichever question happens to come first.
                if float(bbox[1]) < 40 or float(bbox[3]) > page.rect.height - 30:
                    continue
                raw = block.get("image") or b""
                width = int(block.get("width") or 0)
                height = int(block.get("height") or 0)
                if len(raw) < 256 or width < 48 or height < 48:
                    continue
                ext = "." + str(block.get("ext") or "png").lower()
                admitted = budget.try_admit(ext, raw)
                if admitted is None:
                    continue
                images.setdefault(admitted.name, {})["record"] = _image_record(
                    admitted.data, admitted.name
                )
                items.append((float(bbox[1]), float(bbox[0]), build_marker(admitted)))
            ordered = sorted(items, key=lambda item: (item[0], item[1]))
            pages.append("\n".join(text for _y, _x, text in ordered))
    finally:
        document.close()
    text = "\n\n".join(pages)
    records = {name: entry["record"] for name, entry in images.items()}
    return text, records, len(pages), bool(records)


def _extract_docx_rich(data: bytes) -> tuple[str, dict[str, dict], int, bool]:
    budget = ImageBudget(
        max_images=MAX_TOTAL_IMAGES,
        max_total_bytes=MAX_TOTAL_IMAGE_BYTES,
        max_image_bytes=MAX_IMAGE_BYTES,
        min_image_bytes=256,
        max_inline_edge=0,
    )
    try:
        rich = extract_docx_rich(data, budget=budget)
    except Exception as exc:
        raise DocumentImportError(f"The DOCX cannot be opened: {exc}") from exc
    records = {
        image.name: _image_record(image.data, image.name) for image in rich.collection.images
    }
    return "\n\n".join(rich.paragraphs), records, 1, bool(records)


def _strip_markers(text: str) -> str:
    return _MARKER_RE.sub("", text or "").strip()


def _marker_names(text: str) -> list[str]:
    return [match.group(2) for match in _MARKER_RE.finditer(text or "")]


def _body_text(text: str) -> str:
    """Text that can carry a question, ignoring headings and page furniture."""
    lines = []
    for line in _strip_markers(text).splitlines():
        clean = line.strip()
        if len(clean) < 8:
            continue
        if re.match(r"^第\s*\d+\s*[题页]", clean):
            continue
        lines.append(clean)
    return "\n".join(lines)


def _looks_scanned(text: str, pages: int, has_images: bool, kind: str) -> bool:
    if kind != "pdf":
        return False
    if not has_images:
        return False
    return len(_body_text(text)) < 30 * max(pages, 1)


def _regex_rows(text: str) -> list[dict]:
    blocks = [block.strip() for block in _SPLIT_RE.split(text) if block.strip()] or [text.strip()]
    rows = []
    for block in blocks:
        options = {letter: value.strip() for letter, value in _OPTION_RE.findall(block)}
        answer = (_ANSWER_RE.search(block) or [None, ""])[1].strip()
        explanation = (_EXPLAIN_RE.search(block) or [None, ""])[1].strip()
        body = _ANSWER_RE.sub("", _EXPLAIN_RE.sub("", _OPTION_RE.sub("", block))).strip()
        body = re.sub(r"\n{2,}", "\n", body).strip()
        if not _body_text(body):
            continue
        if options:
            question_type = (
                "multi_choice"
                if re.fullmatch(r"[A-D](?:\s*[,，、/]\s*[A-D])+", answer)
                else "single_choice"
            )
        elif re.search(r"\btrue\b|\bfalse\b|正确|错误", answer, re.I):
            question_type = "true_false"
        else:
            question_type = "short_answer"
        rows.append(
            {
                "question": re.sub(r"\s*\n\s*", " ", body),
                "question_type": question_type,
                "options": options,
                "correct_answer": answer,
                "explanation": explanation,
                "tags": [],
                "user_answer": "",
                "figure_boxes": [],
            }
        )
    return rows


def _parse_json_rows(reply: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", reply.strip(), flags=re.S)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("The model reply contained no JSON array")
    data = json.loads(text[start : end + 1])
    if isinstance(data, dict):
        data = data.get("questions") or []
    return [row for row in data if isinstance(row, dict)]


def _llm_client():
    from dataclasses import replace

    from deeptutor.services.llm import LLMClient, get_llm_client

    client = get_llm_client()
    binding = str(getattr(getattr(client, "config", None), "binding", "") or "")
    if "deepseek" in binding.lower():
        # DeepSeek's long reasoning can outlive the import request. The
        # conversion task is mechanical, so a non-reasoning call is enough.
        return LLMClient(replace(client.config, reasoning_effort="none"), configure_env=False)
    return client


async def _ask_text_model(text: str) -> list[dict]:
    client = _llm_client()
    prompt = _SCHEMA_PROMPT + "\n\nDOCUMENT TEXT:\n" + text[:MAX_TEXT_CHARS]
    reply = await asyncio.wait_for(
        client.complete(prompt, system_prompt="Return only JSON.", max_tokens=4000),
        timeout=150,
    )
    return _parse_json_rows(reply)


async def _ask_vision_model(page_png: bytes) -> list[dict]:
    client = _llm_client()
    if not client.supports_multimodal_images():
        raise DocumentImportError(
            "This PDF has no text layer and the active model is not vision-capable. "
            "Use a vision model or import a text-based PDF/DOCX."
        )
    reply = await asyncio.wait_for(
        client.complete(
            _SCHEMA_PROMPT,
            system_prompt="Return only JSON.",
            image_data=base64.b64encode(page_png).decode(),
            image_mime_type="image/png",
            image_filename="page.png",
            max_tokens=4000,
        ),
        timeout=180,
    )
    return _parse_json_rows(reply)


def _render_pdf_pages(data: bytes, limit: int = MAX_VISION_PAGES) -> list[bytes]:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        if len(document) > limit:
            raise DocumentImportError(
                f"This scanned PDF has {len(document)} pages; import at most {limit} at a time."
            )
        return [page.get_pixmap(dpi=VISION_DPI).tobytes("png") for page in document]


def _crop_figure(page_png: bytes, box: Any, margin: float = 0.02) -> bytes | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
    except (TypeError, ValueError):
        return None
    from PIL import Image

    with Image.open(io.BytesIO(page_png)) as image:
        width, height = image.size
        if max(x0, y0, x1, y1) > 1.5:  # tolerate pixel coordinates
            x0, x1 = x0 / width, x1 / width
            y0, y1 = y0 / height, y1 / height
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            return None
        crop = image.convert("RGB").crop(
            (
                int(max(0.0, x0 - margin) * width),
                int(max(0.0, y0 - margin) * height),
                int(min(1.0, x1 + margin) * width),
                int(min(1.0, y1 + margin) * height),
            )
        )
        output = io.BytesIO()
        crop.save(output, "PNG")
        return output.getvalue()


def _parse_json_object(reply: str) -> dict:
    fence = "`" * 3
    text = re.sub(r"^" + fence + r"(?:json)?\s*|\s*" + fence + r"$", "", reply.strip(), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _compose_verification_image(page_png: bytes, figure_png: bytes) -> bytes:
    """Place the full page (for coordinates) beside the current crop."""
    from PIL import Image

    with Image.open(io.BytesIO(page_png)) as page_image:
        page = page_image.convert("RGB")
    with Image.open(io.BytesIO(figure_png)) as figure_image:
        figure = figure_image.convert("RGB")

    page.thumbnail((1000, 1400))
    figure.thumbnail((620, 620))
    gap = 24
    width = page.width + gap + figure.width
    height = max(page.height, figure.height)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(page, (0, 0))
    canvas.paste(figure, (page.width + gap, 0))
    output = io.BytesIO()
    canvas.save(output, "PNG")
    return output.getvalue()


async def _verify_figure(page_png: bytes, figure_png: bytes, question: str) -> dict:
    """Second pass: does this crop actually contain the figure the question needs?"""
    client = _llm_client()
    composite = _compose_verification_image(page_png, figure_png)
    reply = await asyncio.wait_for(
        client.complete(
            _FIGURE_VERIFY_PROMPT + question,
            system_prompt="Return only JSON.",
            image_data=base64.b64encode(composite).decode(),
            image_mime_type="image/png",
            image_filename="verify.png",
            max_tokens=2000,
        ),
        timeout=120,
    )
    return _parse_json_object(reply)


def _svg_to_png(svg: str) -> bytes | None:
    """Rasterize a model-written SVG; PyMuPDF avoids a cairo dependency."""
    if not svg or "</svg>" not in svg.lower() or len(svg) > 300_000:
        return None
    import pymupdf

    try:
        with pymupdf.open(stream=svg.encode("utf-8"), filetype="svg") as document:
            if len(document) == 0:
                return None
            return document[0].get_pixmap(dpi=180, alpha=False).tobytes("png")
    except Exception:
        return None


def _box_values(box: object) -> tuple[float, float, float, float] | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return x0, y0, x1, y1


def _box_area(box: tuple[float, float, float, float]) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])


def _box_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def _expand_box(
    box: tuple[float, float, float, float], pad: float
) -> tuple[float, float, float, float]:
    return (max(0.0, box[0] - pad), max(0.0, box[1] - pad),
            min(1.0, box[2] + pad), min(1.0, box[3] + pad))


def _box_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
    tolerance: float = 0.01,
) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


async def _refine_figures(
    questions: list[dict], page_png: bytes, budget: list[int]
) -> None:
    """Verify each cropped figure; accept a re-crop/redraw only if it also verifies."""
    for question in questions:
        for index, image in enumerate(list(question.get("question_images") or [])):
            if budget[0] <= 0:
                return
            budget[0] -= 1
            try:
                verdict = await _verify_figure(page_png, image["data"], question["question"])
            except Exception:
                continue
            if not verdict or verdict.get("complete"):
                continue
            original = _box_values(image.get("_box"))
            candidates: list[tuple[bytes, tuple[float, float, float, float] | None]] = []
            # A redrawn diagram is usually cleaner than a fragile coordinate fix.
            if verdict.get("redraw_svg"):
                redrawn = _svg_to_png(str(verdict["redraw_svg"]))
                if redrawn:
                    candidates.append((redrawn, original))
            for box in (verdict.get("corrected_boxes") or [])[:2]:
                candidate_box = _box_values(box)
                if candidate_box is None:
                    continue
                if original is not None:
                    # Only accept a correction that keeps the original figure
                    # inside it: a shifted/tiny box is a model mistake, not a fix.
                    if not _box_contains(candidate_box, original):
                        continue
                    if _box_area(candidate_box) > max(0.02, _box_area(original) * 4):
                        continue
                crop = _crop_figure(page_png, candidate_box, margin=0.015)
                if crop:
                    candidates.append((crop, candidate_box))
                break
            replacement = None
            replacement_box = None
            for candidate, candidate_box in candidates:
                if budget[0] <= 0:
                    break
                budget[0] -= 1
                try:
                    check = await _verify_figure(page_png, candidate, question["question"])
                except Exception:
                    continue
                if check.get("complete"):
                    replacement = candidate
                    replacement_box = candidate_box
                    break
            if replacement:
                stem = Path(str(image.get("filename") or "figure.png")).stem
                record = _image_record(replacement, f"{stem}.png")
                if replacement_box is not None:
                    record["_box"] = list(replacement_box)
                question["question_images"][index] = record


def _attach_images(row: dict, images: dict[str, dict], page_png: bytes | None = None) -> None:
    records: list[dict] = []
    seen: set[str] = set()
    for field in ("question", "explanation"):
        for name in _marker_names(str(row.get(field) or "")):
            if name in seen or name not in images:
                continue
            seen.add(name)
            records.append(dict(images[name]))
    for name in row.get("image_refs") or []:
        name = str(name)
        if name in seen or name not in images:
            continue
        seen.add(name)
        records.append(dict(images[name]))
    if page_png is not None:
        for index, box in enumerate(row.get("figure_boxes") or []):
            crop = _crop_figure(page_png, box)
            if crop is None:
                continue
            name = f"figure-{index + 1:02d}.png"
            record = _image_record(crop, name)
            record["_box"] = list(box) if isinstance(box, (list, tuple)) else []
            records.append(record)
    for field in ("question", "explanation"):
        if row.get(field):
            row[field] = _strip_markers(str(row[field]))
    row["question_images"] = records[:MAX_IMAGES_PER_QUESTION]


def _prepare_rows(rows: list[dict], images: dict[str, dict], page_png: bytes | None = None) -> dict:
    questions, errors = [], []
    for number, row in enumerate(rows, 2):
        try:
            _attach_images(row, images, page_png)
            raw = {
                "question": row.get("question") or "",
                "question_type": row.get("question_type") or "",
                "options": row.get("options") or {},
                "correct_answer": row.get("correct_answer") or "",
                "explanation": row.get("explanation") or "",
                "tags": row.get("tags") or [],
                "user_answer": row.get("user_answer") or "",
                "__images__": row.get("question_images") or [],
            }
            if not _strip_markers(raw["question"]):
                continue
            questions.append(_importing.normalize_question(raw))
        except (ValueError, TypeError) as exc:
            errors.append({"row": number, "message": str(exc)})
    return {"questions": questions, "errors": errors}


async def preview_document(data: bytes, filename: str) -> dict:
    if not data or len(data) > _importing.MAX_BYTES:
        raise ValueError("Choose a non-empty file up to 5 MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text, images, pages, has_images = await asyncio.to_thread(_extract_pdf_rich, data)
    elif suffix == ".docx":
        text, images, pages, has_images = await asyncio.to_thread(_extract_docx_rich, data)
    else:
        raise ValueError("Document import supports .pdf and .docx")

    if _looks_scanned(text, pages, has_images, suffix.lstrip(".")):
        page_pngs = await asyncio.to_thread(_render_pdf_pages, data)
        questions, errors = [], []
        verification_budget = [MAX_FIGURE_VERIFICATIONS]
        for page_number, page_png in enumerate(page_pngs, 1):
            try:
                rows = await _ask_vision_model(page_png)
            except DocumentImportError:
                raise
            except Exception as exc:
                errors.append({"row": page_number, "message": f"Page {page_number}: {exc}"})
                continue
            prepared = _prepare_rows(rows, {}, page_png)
            await _refine_figures(prepared["questions"], page_png, verification_budget)
            for error in prepared["errors"]:
                error["row"] = page_number * 1000 + error["row"]
            questions.extend(prepared["questions"])
            errors.extend(prepared["errors"])
        if not questions and not errors:
            raise ValueError("The PDF contains no questions")
        if len(questions) > 500:
            raise ValueError("Import at most 500 questions at a time")
        return {"questions": questions, "errors": errors}

    if not _body_text(text):
        raise ValueError("The document contains no readable text or questions")

    rows = _regex_rows(text)
    missing_answer = any(not str(row.get("correct_answer") or "").strip() for row in rows)
    errors: list[dict] = []
    if rows and not missing_answer:
        prepared = _prepare_rows(rows, images)
    else:
        try:
            llm_rows = await _ask_text_model(text)
        except Exception as exc:
            if not rows:
                raise ValueError(f"Could not structure this document: {exc}") from exc
            prepared = _prepare_rows(rows, images)
            errors = prepared["errors"]
        else:
            prepared = _prepare_rows(llm_rows or rows, images)
    errors.extend(prepared["errors"])
    if not prepared["questions"] and not errors:
        raise ValueError("No questions were found in this document")
    if len(prepared["questions"]) > 500:
        raise ValueError("Import at most 500 questions at a time")
    return {"questions": prepared["questions"], "errors": errors}
