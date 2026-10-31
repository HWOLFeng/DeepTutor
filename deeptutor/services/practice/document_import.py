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
import logging
from pathlib import Path
import re
from typing import Any

from deeptutor.utils.document_images import (
    ImageBudget,
    build_marker,
    compress_image,
    extract_docx_rich,
)

# ``importing`` owns the canonical validators; importing the module (not the
# symbols) keeps this table in one place without a circular value import.
from . import importing as _importing
from .importing import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_QUESTION,
    MAX_TOTAL_IMAGE_BYTES,
    MAX_TOTAL_IMAGES,
)

logger = logging.getLogger(__name__)

MAX_VISION_PAGES = 20
MAX_TEXT_CHARS = 60_000
MAX_FIGURE_VERIFICATIONS = 20
#: A dense page (a sheet of net diagrams) needs room for every row.
MAX_PAGE_TOKENS = 8000
VISION_DPI = 150

# Several screenshots of one worksheet import as one batch, so the caps cover
# the group rather than a single page.
MAX_IMPORT_IMAGES = 20
MAX_IMAGE_IMPORT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_IMPORT_TOTAL_BYTES = 24 * 1024 * 1024
_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

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

_SCHEMA_PROMPT = """You transcribe ONE page of a Chinese school worksheet into practice questions.
The page may hold printed text, the learner's handwriting, teacher ticks/corrections and printed answers.

Return ONLY a JSON array. One element per question:
{"question": str, "question_type": "single_choice"|"multi_choice"|"true_false"|"short_answer",
 "options": {"A": str, ...}, "correct_answer": str, "answer_source": "printed"|"solved",
 "explanation": str, "user_answer": str, "tags": [str],
 "page_type": "exercises"|"knowledge_card"|"answer_key",
 "figure_kind": "none"|"geometry"|"graph"|"net"|"table"|"photo"|"text",
 "figure_boxes": [[x0,y0,x1,y1], ...], "figure_svg": str|null,
 "figure_data": {"kind": "net", "cells": [[row, col, label], ...]} | null}

ROLES — the rule that matters most
- PRINTED text is the question. HANDWRITING is never part of it.
- A printed blank the learner filled in stays a blank: "（　　）" or "＿＿＿＿".
  Never copy the handwritten word into the sentence.
- Handwriting (filled blanks, circled numbers, ticks, crosses, 订正 lines, worked
  steps) goes to user_answer — only the final answer for THIS question, not the
  working. Teacher marks are dropped.
- If the page prints its own answers (知识卡 / 答案页), copy them into
  correct_answer with answer_source="printed"; leave user_answer empty unless the
  learner wrote something.

QUESTION
- Keep formulas, units, punctuation and sub-question order exactly; drop the item
  number ("3.") and page headers/footers/logos.
- Every formula in inline LaTeX $...$: \\frac{}{}, \\sqrt{}, ^{}, \\times, \\div.
  Units go outside the math.
- Choice questions: transcribe every printed option (A–J), body only, no letter
  prefix. When the choices are drawn as a figure (①②③④ in a table), write them
  as options and leave figure_boxes empty.
- 判断题: correct_answer is "T" or "F" (对/√ → T, 错/× → F).
- A fill-in with several blanks: join the answers in order with "; ".
- Never invent a question, option, answer, label or number.

ANSWERS
- answer_source="printed" when the page prints the answer; otherwise solve it
  yourself and use answer_source="solved".
- explanation: one or two sentences of reasoning, not a repeat of the answer.

FIGURES
- figure_boxes: tight normalized [x0,y0,x1,y1] (0-1, origin top-left) around a
  figure THIS question needs. Never include question text, options, headers or
  logos; never box the whole page.
- figure_kind="table": transcribe the table into the question text AND return
  its box, so a misread number can still be checked against the photograph.
- For geometry / coordinate / net diagrams also return figure_svg: a
  self-contained SVG (no scripts, no external images, labels as <text>, viewBox
  matching the figure) that redraws the PRINTED figure with every label,
  dimension and vertex.
- Two questions on one page may share an identical printed stem; the figure is
  what distinguishes them, so give each its own box and never merge them.
- Keep figure_svg under 1200 characters, and omit it for photographs, tables
  and anything you cannot redraw faithfully.
- For a cube net (展开图), return figure_data with the printed squares as
  0-based [row, col, label]: row 0 is the topmost square, col 0 the leftmost,
  and label is the number or letter printed inside that square. Report the grid
  as printed, never the folded cube.

Return at most 40 questions per page, and [] when the page holds no question.
"""

_ANSWER_CHECK_PROMPT = """You check the answers of questions extracted from ONE worksheet page.
The page is attached. For every item below: solve it yourself from the page, then
compare your answer with the given one.

Return ONLY a JSON array:
[{"index": 0, "answer": "<your answer>", "agrees": true|false, "reason": "<short>"}]

Rules:
- Answer in the shape the question expects: option letters ("A", "A,C" for
  multi-select), "T"/"F" for 判断题, the text for 填空, blanks joined by "; ".
- If the page prints the answer, use the printed one.
- Never rewrite a question, never drop an index, never add an item.
- agrees=false only when your answer and the given one are different answers,
  not merely written differently.
"""

_MARKER_RE = re.compile(r"\[图片\s*(\d+):\s*([^\]\s]+)\]")
_OPTION_RE = re.compile(r"^\s*([A-J])\s*[.、．:：)]\s*(.+?)\s*$", re.M)
_ANSWER_RE = re.compile(r"^\s*(?:参考)?答案\s*[:：]?\s*(.+?)\s*$", re.M)
_EXPLAIN_RE = re.compile(r"^\s*(?:解析|分析|解答|详解)\s*[:：]?\s*(.+?)\s*$", re.M)
# A line-leading "3." starts a question; "1.5米" is a number, not a number.
_SPLIT_RE = re.compile(
    r"(?m)^\s*(?:第\s*\d+\s*题[^\n]*|\d{1,3}\s*[.、．](?!\d)|（\s*\d{1,3}\s*）)\s*"
)


class DocumentImportError(ValueError):
    """A PDF/DOCX cannot be turned into practice rows."""


def _report(callback, done: int, total: int, stage: str) -> None:
    """Tell the caller how far a long import got.

    A failing progress callback must never fail the import: the number is for
    the browser, the questions are for the learner.
    """
    if callback is None:
        return
    try:
        callback(done, total, stage)
    except Exception:
        logger.debug("import progress callback failed", exc_info=True)


def _image_record(data: bytes, name: str, *, optimise: bool = True) -> dict:
    if optimise:
        # Crops, embedded figures and photos are stored once, so this is the
        # place that keeps the question bank's image footprint down. Uploads
        # are already compressed for the model call and pass optimise=False.
        suffix, data = compress_image(Path(name).suffix, data)
        name = f"{Path(name).stem or 'image'}{suffix}"
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


def _looks_scanned(text: str, pages: int, kind: str) -> bool:
    """Whether a PDF has to be read as page images instead of as text.

    The presence of an image is the wrong signal: a scanned page is one
    full-page image, which the header/footer filter drops before it can set a
    flag, while a text page can legitimately carry a large background picture.
    Judge the text layer instead, and treat CID-mangled output (fonts without a
    ToUnicode map) as no text at all.
    """
    if kind != "pdf":
        return False
    clean = _strip_markers(text)
    if "(cid:" in clean[:4000]:
        return True
    return len(_body_text(clean)) < 30 * max(pages, 1)


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


def _active_model_vision() -> object:
    """The settings picker's vision verdict for the active model, when known.

    A freshly configured vision model may be declared only in the catalog and
    not in the process-global capability table. Reading the picker's verdict
    here keeps the importer from vetoing a model the user was told is
    vision-capable.
    """
    try:
        from deeptutor.services.config.model_catalog import get_model_catalog_service
        from deeptutor.services.model_selection.llm import list_llm_options

        for option in list_llm_options(get_model_catalog_service().load())["options"]:
            if option.get("is_active_default") and type(option.get("supports_vision")) is bool:
                return option["supports_vision"]
    except Exception:
        return None
    return None


def _client_supports_vision(client: Any) -> bool:
    """Vision support for the client actually used, the picker's verdict first."""
    from deeptutor.services.llm.capabilities import supports_vision_declared

    config = getattr(client, "config", None)
    declared = _active_model_vision()
    if supports_vision_declared(
        declared, str(getattr(config, "binding", "") or ""), getattr(config, "model", None)
    ):
        return True
    return bool(client.supports_multimodal_images())


def _vision_unavailable_error(client: Any, subject: str) -> DocumentImportError:
    model = str(getattr(getattr(client, "config", None), "model", "") or "the active model")
    return DocumentImportError(
        f"{model} cannot read images, so {subject}. Switch to a vision-capable model in "
        "Settings → Models, or import a text-based PDF/DOCX."
    )


async def _ask_vision_model(
    page_png: bytes,
    *,
    mime_type: str = "image/png",
    filename: str = "page.png",
) -> list[dict]:
    client = _llm_client()
    if not _client_supports_vision(client):
        raise _vision_unavailable_error(client, "these pages cannot be imported")
    reply = await asyncio.wait_for(
        client.complete(
            _SCHEMA_PROMPT,
            system_prompt="Return only JSON.",
            image_data=base64.b64encode(page_png).decode(),
            image_mime_type=mime_type,
            image_filename=filename,
            max_tokens=MAX_PAGE_TOKENS,
        ),
        timeout=180,
    )
    try:
        return _parse_json_rows(reply)
    except ValueError:
        # A dense page (11 net diagrams, each with its own SVG) can exceed the
        # reply budget and arrive truncated. Re-ask once without redraws rather
        # than losing every question on the page.
        logger.warning("vision reply for %s was not valid JSON; retrying without SVGs", filename)
        retry = await asyncio.wait_for(
            client.complete(
                _SCHEMA_PROMPT
                + "\nIMPORTANT: the previous reply was cut off. Return every row again "
                "with \"figure_svg\": null and no commentary.",
                system_prompt="Return only JSON.",
                image_data=base64.b64encode(page_png).decode(),
                image_mime_type=mime_type,
                image_filename=filename,
                max_tokens=MAX_PAGE_TOKENS,
            ),
            timeout=180,
        )
        return _parse_json_rows(retry)


MAX_ANSWER_CHECKS = 40


async def _check_answers(
    rows: list[dict],
    page_png: bytes,
    *,
    mime_type: str = "image/png",
    filename: str = "page.png",
) -> list[dict]:
    """Second opinion on every answer the model solved itself.

    A transcription pass that guesses a cube-net answer can contradict the
    next page's guess for the same figure. One extra call asks the model to
    solve the listed questions from the page again; a disagreement is surfaced
    to the learner instead of being silently trusted.
    """
    if not rows or len(rows) > MAX_ANSWER_CHECKS:
        return []
    client = _llm_client()
    if not _client_supports_vision(client):
        return []
    listing = [
        {
            "index": index,
            "question": str(row.get("question") or ""),
            "question_type": str(row.get("question_type") or ""),
            "options": row.get("options") or {},
            "answer": str(row.get("correct_answer") or ""),
            "answer_source": str(row.get("answer_source") or ""),
        }
        for index, row in enumerate(rows)
    ]
    reply = await asyncio.wait_for(
        client.complete(
            _ANSWER_CHECK_PROMPT + "\n\nQUESTIONS:\n" + json.dumps(listing, ensure_ascii=False),
            system_prompt="Return only JSON.",
            image_data=base64.b64encode(page_png).decode(),
            image_mime_type=mime_type,
            image_filename=filename,
            max_tokens=3000,
        ),
        timeout=180,
    )
    return _parse_json_rows(reply)


def _merge_verdicts(rows: list[dict], verdicts: list[dict]) -> None:
    """Attach the second opinion to the rows it belongs to.

    The original answer is never replaced on a disagreement: two guesses are
    not evidence, and quietly swapping one for the other is how a wrong answer
    gets laundered into the question bank. Only an *empty* answer is filled in,
    and the disagreement is recorded for the preview to show.
    """
    answers_by_index: dict[int, dict] = {}
    for verdict in verdicts:
        try:
            index = int(verdict.get("index"))
        except (TypeError, ValueError):
            continue
        answers_by_index[index] = verdict
    for index, row in enumerate(rows):
        verdict = answers_by_index.get(index)
        if verdict is None:
            continue
        answer = str(verdict.get("answer") or "").strip()
        agrees = verdict.get("agrees")
        if agrees is False:
            row["answer_conflict"] = True
            row["verdict_answer"] = answer
            # A row whose own answer is blank has nothing to lose: use the
            # second opinion rather than importing a question nobody can grade.
            if not str(row.get("correct_answer") or "").strip() and answer:
                row["correct_answer"] = answer
        elif agrees is True and answer:
            row["answer_verified"] = True
        row["verdict_answer"] = row.get("verdict_answer") or answer


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
            if image.get("_redrawn"):
                # A model-drawn figure has no pixel relationship with the page;
                # the completeness check would only thrash it.
                continue
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
    # A model-drawn SVG is accepted in place of a crop. It is crisp and
    # reflows, which is what a geometry figure wants; the preview shows it
    # before the learner commits, and a bad one is simply deselected.
    svg = row.get("figure_svg")
    if isinstance(svg, str) and svg.strip():
        redrawn = _svg_to_png(svg)
        if redrawn:
            record = _image_record(redrawn, "figure-redrawn.png")
            record["_redrawn"] = True
            records.append(record)
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
    if page_png is not None and not any(record.get("_redrawn") for record in records):
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


def _verify_row(row: dict) -> None:
    """Let a deterministic verifier outvote the model when it can.

    The second model pass is a second opinion; arithmetic is a proof. A
    verifier that disagrees marks the row for the preview, and one that agrees
    clears a model-side disagreement rather than leaving a false alarm.
    """
    from .verify import verify_answer

    verdict = verify_answer(
        str(row.get("question") or ""),
        str(row.get("correct_answer") or ""),
        options=row.get("options") or {},
        qtype=str(row.get("question_type") or ""),
        figure_data=row.get("figure_data") or None,
    )
    if verdict is None or not verdict.decided:
        return
    row["verification"] = verdict.reason
    if verdict.status == "agree":
        row["answer_verified"] = True
        row.pop("answer_conflict", None)
        row.pop("verdict_answer", None)
        return
    row["answer_conflict"] = True
    if verdict.answer:
        row["verdict_answer"] = verdict.answer


def _figure_key(question: dict) -> tuple:
    """The figure a question is about, for duplicate detection."""
    key = []
    for image in question.get("question_images") or []:
        box = image.get("_box")
        if isinstance(box, (list, tuple)) and len(box) == 4:
            key.append(tuple(round(float(value), 2) for value in box))
        elif image.get("_redrawn"):
            key.append(("redrawn", str(image.get("filename") or "")))
    return tuple(key)


def merge_duplicate_questions(questions: list[dict]) -> tuple[list[dict], int]:
    """One figure, one question: drop repeats of the same stem on the same figure.

    A dense page can yield the same row twice. Two rows with the same stem but
    different figures are *not* duplicates — that is what a sheet of nets looks
    like — so the figure is part of the identity.
    """
    seen: set[tuple] = set()
    kept: list[dict] = []
    for question in questions:
        key = (question.get("question_id") or "", _figure_key(question))
        if key in seen:
            continue
        seen.add(key)
        kept.append(question)
    return kept, len(questions) - len(kept)


def _prepare_rows(rows: list[dict], images: dict[str, dict], page_png: bytes | None = None) -> dict:
    from .quality import classify_attempt, strip_answer_leak

    questions, errors = [], []
    for number, row in enumerate(rows, 2):
        try:
            _attach_images(row, images, page_png)
            question = str(row.get("question") or "")
            if not _strip_markers(question):
                continue
            options = row.get("options") or {}
            raw = {
                "question": question,
                "question_type": row.get("question_type") or "",
                "options": options,
                "correct_answer": row.get("correct_answer") or "",
                "explanation": row.get("explanation") or "",
                "tags": row.get("tags") or [],
                "user_answer": row.get("user_answer") or "",
                "__images__": row.get("question_images") or [],
            }
            cleaned, removed = strip_answer_leak(
                question,
                [str(raw["correct_answer"]), str(raw["user_answer"])],
                question_type=str(raw["question_type"]),
                options=options if isinstance(options, dict) else {},
            )
            raw["question"] = cleaned
            _verify_row(row)
            item = _importing.normalize_question(raw)
            item["leaks"] = removed
            item["attempt"] = classify_attempt(
                item["user_answer"],
                item["correct_answer"],
                item["question_type"],
                item["options"],
            )
            item["answer_source"] = str(row.get("answer_source") or "solved")
            item["answer_conflict"] = bool(row.get("answer_conflict"))
            item["verdict_answer"] = str(row.get("verdict_answer") or "")
            item["verification"] = str(row.get("verification") or "")
            item["page_type"] = str(row.get("page_type") or "exercises")
            item["figure_data"] = row.get("figure_data") or None
            questions.append(item)
        except (ValueError, TypeError) as exc:
            errors.append({"row": number, "message": str(exc)})
    return {"questions": questions, "errors": errors}


async def _import_vision_pages(
    pages: list[tuple[bytes, str, str]],
    *,
    keep_source_image: bool = False,
    on_progress=None,
) -> dict:
    """Turn rendered pages or user images into rows, one model call per page.

    ``pages`` holds ``(bytes, mime_type, display name)``. A page that yields a
    single question with no marked figure keeps its own picture, so a photo of
    one exercise still shows the exercise after import.
    """
    questions: list[dict] = []
    errors: list[dict] = []
    budget = [MAX_FIGURE_VERIFICATIONS]
    for number, (data, mime, name) in enumerate(pages, 1):
        _report(on_progress, number - 1, len(pages), "recognizing")
        try:
            rows = await _ask_vision_model(data, mime_type=mime, filename=name)
        except DocumentImportError:
            raise
        except Exception as exc:
            errors.append({"row": number, "message": f"{name}: {exc}"})
            continue
        if rows and any(str(row.get("answer_source") or "") != "printed" for row in rows):
            _report(on_progress, number - 1, len(pages), "verifying")
            try:
                verdicts = await _check_answers(rows, data, mime_type=mime, filename=name)
            except Exception:
                # A verification call that fails must not cost the page its rows.
                logger.warning("answer check failed for %s", name, exc_info=True)
                verdicts = []
            _merge_verdicts(rows, verdicts)
        prepared = _prepare_rows(rows, {}, data)
        await _refine_figures(prepared["questions"], data, budget)
        for error in prepared["errors"]:
            error["row"] = number * 1000 + error["row"]
        if (
            keep_source_image
            and len(prepared["questions"]) == 1
            and not prepared["questions"][0]["question_images"]
        ):
            prepared["questions"][0]["question_images"] = [
                _image_record(data, name, optimise=False)
            ]
        questions.extend(prepared["questions"])
        errors.extend(prepared["errors"])
        _report(on_progress, number, len(pages), "recognizing")
    if len(questions) > 500:
        raise ValueError("Import at most 500 questions at a time")
    questions, merged = merge_duplicate_questions(questions)
    return {"questions": questions, "errors": errors, "duplicates_merged": merged}


def _check_image(data: bytes, name: str) -> None:
    """Reject renamed or corrupt files before spending a model call on them."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError(f"{name} is not a readable PNG, JPEG, WebP or GIF image") from exc


async def preview_images(images: list[tuple[bytes, str]], on_progress=None) -> dict:
    """Import several screenshots or photos of questions as one batch."""
    if not images:
        raise ValueError("Select at least one image")
    if len(images) > MAX_IMPORT_IMAGES:
        raise ValueError(f"Import at most {MAX_IMPORT_IMAGES} images at a time")
    pages: list[tuple[bytes, str, str]] = []
    total = 0
    _report(on_progress, 0, len(images), "preparing")
    for data, name in images:
        suffix = Path(name).suffix.lower()
        if suffix not in _IMAGE_MIME:
            raise ValueError(f"{name}: use a PNG, JPEG, WebP or GIF image")
        if not data:
            raise ValueError(f"{name} is empty")
        if len(data) > MAX_IMAGE_IMPORT_BYTES:
            raise ValueError(
                f"{name} is larger than {MAX_IMAGE_IMPORT_BYTES // (1024 * 1024)} MB"
            )
        _check_image(data, name)
        compressed_ext, compressed = await asyncio.to_thread(compress_image, suffix, data)
        name = f"{Path(name).stem or 'image'}{compressed_ext}"
        total += len(compressed)
        pages.append((compressed, _IMAGE_MIME[compressed_ext], name))
    if total > MAX_IMAGE_IMPORT_TOTAL_BYTES:
        raise ValueError(
            f"These images total more than {MAX_IMAGE_IMPORT_TOTAL_BYTES // (1024 * 1024)} MB"
        )
    client = _llm_client()
    if not _client_supports_vision(client):
        raise _vision_unavailable_error(client, "these images cannot be imported")
    parsed = await _import_vision_pages(
        pages, keep_source_image=True, on_progress=on_progress
    )
    if not parsed["questions"] and not parsed["errors"]:
        raise ValueError("No questions were found in these images")
    return parsed


async def preview_document(data: bytes, filename: str, on_progress=None) -> dict:
    if not data or len(data) > _importing.MAX_BYTES:
        raise ValueError("Choose a non-empty file up to 5 MB")
    suffix = Path(filename).suffix.lower()
    _report(on_progress, 0, 1, "reading")
    if suffix == ".pdf":
        text, images, pages, _has_images = await asyncio.to_thread(_extract_pdf_rich, data)
    elif suffix == ".docx":
        text, images, pages, _has_images = await asyncio.to_thread(_extract_docx_rich, data)
    else:
        raise ValueError("Document import supports .pdf and .docx")

    text_rows = _regex_rows(text)
    # A sparse text layer can still be parsed (a one-question PDF with a big
    # figure, for instance), so only page images are used when the text itself
    # cannot carry questions. That keeps a text-based PDF off the vision path
    # even when the active model cannot read images.
    if _looks_scanned(text, pages, suffix.lstrip(".")) and not text_rows:
        client = _llm_client()
        if not _client_supports_vision(client):
            # Fail before rendering 20 pages, and name the model the user has
            # to change instead of claiming the PDF has no text at all.
            raise _vision_unavailable_error(
                client, "this image-only PDF (no usable text layer) cannot be imported"
            )
        page_pngs = await asyncio.to_thread(_render_pdf_pages, data)
        parsed = await _import_vision_pages(
            [
                (page_png, "image/png", f"page-{number:02d}.png")
                for number, page_png in enumerate(page_pngs, 1)
            ],
            on_progress=on_progress,
        )
        if not parsed["questions"] and not parsed["errors"]:
            raise ValueError("The PDF contains no questions")
        return parsed

    if not _body_text(text):
        raise ValueError("The document contains no readable text or questions")

    rows = text_rows
    _report(on_progress, 0, 1, "structuring")
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
    questions, merged = merge_duplicate_questions(prepared["questions"])
    _report(on_progress, 1, 1, "structuring")
    return {"questions": questions, "errors": errors, "duplicates_merged": merged}
