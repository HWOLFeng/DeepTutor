---
name: practice-template
description: Convert a PDF, Word (.docx), TXT, or Markdown question set or mistake notebook into the standard practice-template .xlsx workbook (question, question_type, A-D options, correct_answer, explanation, tags, user_answer), keeping question figures as floating images. Use when the user asks to turn a paper, exam, worksheet, quiz, 错题本, 练习卷, or question bank into an Excel/答题/练习模板 spreadsheet.
tags:
- tool
- office
- practice
requires:
  sandbox: shell
---

# Practice template: PDF / Word → XLSX

Build a ten-column practice workbook that opens as a ready-to-use question bank
matching the user's `practice-template.xlsx`. One question per row, fixed header,
no extra sheets.

## Output contract

| Col | Header | Rule |
| --- | --- | --- |
| A | `question` | Full stem, source language, no item number, no "第N题" heading |
| B | `question_type` | `single_choice`, `multi_choice`, `true_false`, `short_answer` |
| C-F | `A`, `B`, `C`, `D` | Option content only (no `A.` prefix); blank when absent |
| G | `correct_answer` | Letter / `A,B` / `true` / `false` / answer text |
| H | `explanation` | Concise worked solution |
| I | `tags` | Comma-separated, no spaces (`长度单位,减法`) |
| J | `user_answer` | The student's marked choice, or blank |

Keep exactly this header row and sheet name `Sheet`. Figures are inserted as
floating pictures in columns K-N on the question's row, so the same workbook
can be re-imported into the question bank without losing them.

Read `references/practice-schema.md` for ambiguity rules, per-type examples,
and mistake-notebook conventions.

## Workflow

1. **Locate the input.** Use `workspace_list` / `workspace_search` /
   `workspace_read` to confirm the file and its **workspace-relative** path.
   Never guess a host absolute path.
2. **Run the converter.** In `exec` (`language: "python"`), paste the program
   below verbatim and edit only `SOURCE` and `OUTPUT` (workspace-relative).
   It extracts text and figures from PDF/DOCX/TXT/MD, uses the configured
   DeepTutor model to structure questions, falls back to a regex parser with
   `USE_LLM = "off"`, renders scanned PDF pages and reads them with the vision
   model (cropping the figures it marks, then re-checking each crop and
   re-cropping or redrawing it as SVG when the check fails), and writes the
   workbook plus `<output>.questions.json`.
3. **Read the result.** The program prints `rows`, `figures`, `mode`, and `warnings`.
   If `mode` is `heuristic`, spot-check types and answers. If `warnings` is
   non-empty, fix those rows before presenting.
4. **Repair if needed.** Edit `<output>.questions.json` (same keys as the
   columns), then rerun with `INPUT_JSON` set and a new `OUTPUT`. This is the
   only supported way to correct extraction without re-reading the source.
   The repair JSON lists figure filenames only; rerun from the source if
   figures must be rebuilt.
5. **Validate.** Reopen the workbook in a short `exec` call: header matches
   A1:J1, every question non-empty, every `question_type` allowed, choice rows
   have at least two options, `correct_answer` non-empty, and, when
   `figures > 0`, that pictures are anchored to K-N on the matching rows.
   Then present the file with `workspace_present`.

```python
# ═══ practice-template converter (edit SOURCE / OUTPUT only) ═══
"""Convert PDF/DOCX question documents into the practice-template workbook."""

import base64
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

# ── edit SOURCE / OUTPUT; the rest are optional overrides ────────────────
SOURCE = os.environ.get("PRACTICE_SOURCE", "input.pdf")     # workspace-relative or absolute
OUTPUT = os.environ.get("PRACTICE_OUTPUT", "practice.xlsx")
INPUT_JSON = os.environ.get("PRACTICE_QUESTIONS_JSON", "")   # repair: build from this JSON
SETTINGS_DIR = os.environ.get("PRACTICE_SETTINGS_DIR", "")   # override model_catalog location
USE_LLM = os.environ.get("PRACTICE_LLM", "auto")             # auto | off (off = regex parser)
USE_VISION = os.environ.get("PRACTICE_VISION", "auto")       # auto | off
FIGURES = os.environ.get("PRACTICE_FIGURES", "auto")         # auto | off (floating images)
VERIFY_FIGURES = os.environ.get("PRACTICE_VERIFY_FIGURES", "auto")  # auto | off
# ─────────────────────────────────────────────────────────────────────────

HEADERS = ["question", "question_type", "A", "B", "C", "D",
           "correct_answer", "explanation", "tags", "user_answer"]
TYPES = ("single_choice", "multi_choice", "true_false", "short_answer")
TYPE_ALIASES = {"single": "single_choice", "单选": "single_choice", "单选题": "single_choice",
                "multi": "multi_choice", "多选": "multi_choice", "多选题": "multi_choice",
                "judge": "true_false", "判断": "true_false", "判断题": "true_false",
                "fill": "short_answer", "填空": "short_answer", "简答": "short_answer"}
PROMPT = """Convert exam pages / mistake notebooks into practice-template rows.
Return ONLY a JSON array of objects:
{"question","question_type" ("single_choice"|"multi_choice"|"true_false"|"short_answer"),
 "options":{"A","B","C","D"},"correct_answer","explanation","tags":[str],
 "user_answer","figure_boxes":[[x0,y0,x1,y1]]}
Rules: transcribe faithfully in the source language and keep formulas/units and
sub-questions; drop page headers, logos, footers, "第N题" headings and item
numbers ("10."); options hold the content WITHOUT the letter prefix; omit absent
options; correct_answer is choice letter(s), comma-joined for multi-select,
"true"/"false", or answer text, solved by you when not printed; user_answer is
the letter of a tick/check/handwritten mark, otherwise ""; explanation is a
concise solution; tags are 1-3 short topic labels. Return [] when no questions.
Keep any "[图片 N: name]" marker exactly where it belongs in the question text.
figure_boxes: normalized [x0,y0,x1,y1] (0-1, origin top-left) around a figure
that belongs to THIS question; [] when there is no figure. Never box the whole
page, the header or a logo. Never invent questions."""

VERIFY_PROMPT = """Audit one figure crop attached to an exam question.
The image is a composite: LEFT = full page, RIGHT = the crop currently used.
Return ONLY a JSON object:
{"complete": bool, "reason": str,
 "corrected_boxes": [[x0,y0,x1,y1], ...], "redraw_svg": str|null}
Rules:
- complete=true only when the RIGHT crop contains the whole figure the question
  needs, with no cut-off edges and no unrelated question text/options/header.
- If a re-crop can fix it, corrected_boxes are normalized 0-1 relative to the
  LEFT page and MUST fully contain the current crop; never a label-only box.
- If cropping cannot fix it (vector/cut-off/overlaid text), return a faithful
  self-contained SVG in redraw_svg (no scripts/external assets; labels as
  <text>). For geometry diagrams prefer redraw_svg.
- Use [] / null when you cannot improve it. Never invent labels.
QUESTION:
"""


def workspace_root() -> Path:
    return Path(os.environ.get("DEEPTUTOR_WORKSPACE_ROOT", ".")).resolve()


def resolve(path: str) -> Path:
    candidate = Path(path).expanduser()
    return candidate if candidate.is_absolute() else workspace_root() / candidate


IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
MAX_FIGURES = 200
MARKER_RE = re.compile(r"\[图片\s*\d+:\s*([^\]\s]+)\]")


def _admit_image(images: dict[str, dict], data: bytes, ext: str) -> str | None:
    ext = str(ext or "png").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    if "." + ext not in IMAGE_TYPES:
        return None
    if len(images) >= MAX_FIGURES or len(data) < 256 or len(data) > 4 * 1024 * 1024:
        return None
    name = f"image-{len(images) + 1:02d}.{ext}"
    images[name] = {"filename": name, "mime_type": IMAGE_TYPES["." + ext], "data": data}
    return name


def pdf_rich(source: Path) -> tuple[str, dict[str, dict]]:
    """PDF text with inline figure markers, plus the figure bytes."""
    import pymupdf

    images: dict[str, dict] = {}
    pages = []
    with pymupdf.open(source) as document:
        for page in document:
            items = []
            for block in page.get_text("dict").get("blocks") or []:
                bbox = block.get("bbox") or (0, 0, 0, 0)
                if block.get("type") == 0:
                    lines = []
                    for line in block.get("lines") or []:
                        text = "".join(span.get("text") or "" for span in line.get("spans") or [])
                        if text.strip():
                            lines.append(text)
                    if lines:
                        items.append((float(bbox[1]), float(bbox[0]), "\n".join(lines)))
                elif block.get("type") == 1:
                    if float(bbox[1]) < 40 or float(bbox[3]) > page.rect.height - 30:
                        continue
                    width, height = int(block.get("width") or 0), int(block.get("height") or 0)
                    if width < 48 or height < 48:
                        continue
                    name = _admit_image(images, block.get("image") or b"", block.get("ext") or "png")
                    if name:
                        items.append((float(bbox[1]), float(bbox[0]),
                                      f"[图片 {len(images)}: {name}]"))
            ordered = sorted(items, key=lambda item: (item[0], item[1]))
            pages.append("\n".join(text for _y, _x, text in ordered))
    return "\n\n".join(pages), images


def docx_rich(source: Path) -> tuple[str, dict[str, dict]]:
    """DOCX body text with inline figure markers, plus the figure bytes."""
    import posixpath
    import xml.etree.ElementTree as ET
    import zipfile

    word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    drawing = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    package = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    images: dict[str, dict] = {}
    paragraphs = []
    try:
        with zipfile.ZipFile(source) as archive:
            rels = {}
            try:
                rel_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
                for rel in rel_root.findall(package + "Relationship"):
                    if rel.get("TargetMode") != "External":
                        rels[rel.get("Id")] = rel.get("Target")
            except KeyError:
                pass
            root = ET.fromstring(archive.read("word/document.xml"))
            for paragraph in root.iter(word + "p"):
                text, markers, seen = [], [], set()
                for element in paragraph.iter():
                    if element.tag == word + "t" and element.text:
                        text.append(element.text)
                    elif element.tag == word + "tab":
                        text.append("\t")
                    elif element.tag in {word + "br", word + "cr"}:
                        text.append("\n")
                    elif element.tag == drawing + "blip":
                        rid = element.get(rel_ns + "embed")
                        if not rid or rid in seen:
                            continue
                        seen.add(rid)
                        target = str(rels.get(rid) or "").lstrip("/")
                        if not target:
                            continue
                        member = target if target.startswith("word/") else posixpath.normpath(
                            posixpath.join("word", target))
                        try:
                            raw = archive.read(member)
                        except KeyError:
                            continue
                        name = _admit_image(images, raw, posixpath.splitext(member)[1])
                        if name:
                            markers.append(f"[图片 {len(images)}: {name}]")
                body = "".join(text).strip()
                if markers:
                    body = (body + "\n" if body else "") + "\n".join(markers)
                if body:
                    paragraphs.append(body)
    except Exception:
        return docx_text(source), images
    return "\n\n".join(paragraphs), images


def docx_text(source: Path) -> str:
    from docx import Document

    document = Document(str(source))
    lines = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_source(source: Path) -> tuple[str, str, dict[str, dict]]:
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        text, images = pdf_rich(source)
        return text, "pdf", images
    if suffix in {".docx", ".docm"}:
        text, images = docx_rich(source)
        return text, "docx", images
    if suffix in {".txt", ".md"}:
        return source.read_text(encoding="utf-8", errors="replace"), "text", {}
    raise ValueError(f"unsupported source type {suffix}; use .pdf, .docx, .txt or .md")


def attach_markers(rows: list[dict], images: dict[str, dict]) -> list[dict]:
    """Move inline markers into row images and strip them from the text."""
    for row in rows:
        found, seen = [], set()
        for field in ("question", "explanation"):
            raw = str(row.get(field) or "")
            for name in MARKER_RE.findall(raw):
                if name in images and name not in seen:
                    seen.add(name)
                    found.append(images[name])
            row[field] = MARKER_RE.sub("", raw).strip()
        row["images"] = (row.get("images") or []) + found
        row.pop("figure_boxes", None)
    return rows


def crop_figure(page_png: bytes, box: object, margin: float = 0.02) -> bytes | None:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
    except (TypeError, ValueError):
        return None
    import io

    from PIL import Image

    with Image.open(io.BytesIO(page_png)) as image:
        width, height = image.size
        if max(x0, y0, x1, y1) > 1.5:
            x0, x1 = x0 / width, x1 / width
            y0, y1 = y0 / height, y1 / height
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        if x1 - x0 < 0.02 or y1 - y0 < 0.02:
            return None
        crop = image.convert("RGB").crop((
            int(max(0.0, x0 - margin) * width), int(max(0.0, y0 - margin) * height),
            int(min(1.0, x1 + margin) * width), int(min(1.0, y1 + margin) * height)))
        output = io.BytesIO()
        crop.save(output, "PNG")
        return output.getvalue()


def _box_values(box):
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    return (x0, y0, x1, y1) if x1 - x0 >= 0.01 and y1 - y0 >= 0.01 else None


def _box_contains(outer, inner, tolerance=0.01):
    return (outer[0] <= inner[0] + tolerance and outer[1] <= inner[1] + tolerance
            and outer[2] >= inner[2] - tolerance and outer[3] >= inner[3] - tolerance)


def _compose_verify(page_png: bytes, figure_png: bytes) -> bytes:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(page_png)) as page_image:
        page = page_image.convert("RGB")
    with Image.open(io.BytesIO(figure_png)) as figure_image:
        figure = figure_image.convert("RGB")
    page.thumbnail((1000, 1400))
    figure.thumbnail((620, 620))
    canvas = Image.new("RGB", (page.width + 24 + figure.width, max(page.height, figure.height)), "white")
    canvas.paste(page, (0, 0))
    canvas.paste(figure, (page.width + 24, 0))
    output = io.BytesIO()
    canvas.save(output, "PNG")
    return output.getvalue()


def _parse_json_object(reply: str) -> dict:
    fence = "`" * 3
    text = re.sub(r"^" + fence + r"(?:json)?\s*|\s*" + fence + r"$", "", reply.strip(), flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _svg_png(svg: str) -> bytes | None:
    if not svg or "</svg>" not in svg.lower() or len(svg) > 300_000:
        return None
    import pymupdf

    try:
        with pymupdf.open(stream=svg.encode("utf-8"), filetype="svg") as document:
            return document[0].get_pixmap(dpi=180, alpha=False).tobytes("png") if len(document) else None
    except Exception:
        return None


def verify_figure(page_png: bytes, figure_png: bytes, question: str, cfg: dict) -> dict:
    data = base64.b64encode(_compose_verify(page_png, figure_png)).decode()
    if "anthropic" in cfg["binding"]:
        content = [{"type": "text", "text": VERIFY_PROMPT + question},
                   {"type": "image", "media_type": "image/png", "data": data}]
    else:
        content = [{"type": "text", "text": VERIFY_PROMPT + question},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}]
    return _parse_json_object(chat(cfg, content, max_tokens=2000))


def refine_figure(page_png: bytes, image: dict, question: str, cfg: dict, budget: list[int]) -> dict:
    if budget[0] <= 0:
        return image
    budget[0] -= 1
    try:
        verdict = verify_figure(page_png, image["data"], question, cfg)
    except Exception:
        return image
    if not verdict or verdict.get("complete"):
        return image
    original = _box_values(image.get("_box"))
    candidates = []
    if verdict.get("redraw_svg"):
        redrawn = _svg_png(str(verdict["redraw_svg"]))
        if redrawn:
            candidates.append((redrawn, original))
    for box in (verdict.get("corrected_boxes") or [])[:2]:
        candidate = _box_values(box)
        if candidate is None:
            continue
        if original is not None and not _box_contains(candidate, original):
            continue
        crop = crop_figure(page_png, candidate)
        if crop:
            candidates.append((crop, candidate))
        break
    for candidate, box in candidates:
        if budget[0] <= 0:
            break
        budget[0] -= 1
        try:
            check = verify_figure(page_png, candidate, question, cfg)
        except Exception:
            continue
        if check.get("complete"):
            updated = dict(image)
            updated["data"] = candidate
            updated["filename"] = str(image.get("filename") or "figure.png").rsplit(".", 1)[0] + ".png"
            updated["mime_type"] = "image/png"
            updated["_box"] = list(box) if box else image.get("_box")
            return updated
    return image


def load_model_config() -> dict | None:
    candidates = []
    if SETTINGS_DIR:
        candidates.append(Path(SETTINGS_DIR) / "model_catalog.json")
    if os.environ.get("DEEPTUTOR_HOME"):
        candidates.append(Path(os.environ["DEEPTUTOR_HOME"]) / "data/user/settings/model_catalog.json")
    candidates += [Path("/app/data/user/settings/model_catalog.json"),
                   Path.cwd() / "data/user/settings/model_catalog.json",
                   Path.cwd().parent / "data/user/settings/model_catalog.json"]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            service = json.loads(path.read_text(encoding="utf-8"))["services"]["llm"]
            profiles = service.get("profiles") or []
            profile = next((p for p in profiles if p.get("id") == service.get("active_profile_id")),
                           profiles[0] if profiles else None)
            models = (profile or {}).get("models") or []
            model = next((m for m in models if m.get("id") == service.get("active_model_id")),
                         models[0] if models else None)
            if profile and model and profile.get("api_key"):
                return {"base_url": str(profile.get("base_url") or "").rstrip("/"),
                        "api_key": profile["api_key"],
                        "model": model.get("model") or model.get("id"),
                        "binding": str(profile.get("binding") or "openai").lower(),
                        "extra_headers": profile.get("extra_headers") or {}}
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None


def chat(cfg: dict, content: list[dict], max_tokens: int = 3000) -> str:
    anthropic = "anthropic" in cfg["binding"]
    if anthropic:
        parts = [{"type": "image", "source": {"type": "base64", "media_type": p["media_type"],
                                              "data": p["data"]}}
                 if p["type"] == "image" else {"type": "text", "text": p["text"]} for p in content]
        url = cfg["base_url"] if cfg["base_url"].endswith("/messages") else (
            cfg["base_url"] + "/messages" if cfg["base_url"].endswith("/v1")
            else cfg["base_url"] + "/v1/messages")
        payload = {"model": cfg["model"], "max_tokens": max_tokens,
                   "messages": [{"role": "user", "content": parts}]}
        headers = {"content-type": "application/json", "x-api-key": cfg["api_key"],
                   "anthropic-version": "2023-06-01"}
    else:
        url = cfg["base_url"] if cfg["base_url"].endswith("/chat/completions") else (
            cfg["base_url"] + "/chat/completions")
        payload = {"model": cfg["model"], "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "user", "content": content}]}
        if "deepseek" in cfg["binding"] or "deepseek" in str(cfg["model"]).lower():
            payload["reasoning_effort"] = "none"  # long reasoning makes page calls time out
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {cfg['api_key']}"}
    headers.update({str(k): str(v) for k, v in cfg.get("extra_headers", {}).items()})
    body, last = json.dumps(payload).encode(), None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode())
            if anthropic:
                return "".join(part.get("text", "") for part in result.get("content", []))
            return result["choices"][0]["message"].get("content") or ""
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            if attempt == 0:
                time.sleep(2)
    raise RuntimeError(f"LLM request failed: {last}")


def parse_json_array(reply: str) -> list[dict]:
    fence = "`" * 3
    text = re.sub(r"^" + fence + r"(?:json)?\s*|\s*" + fence + r"$", "", reply.strip(), flags=re.S)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("model reply contained no JSON array")
    data = json.loads(text[start:end + 1])
    if isinstance(data, dict):
        data = data.get("questions") or []
    return [row for row in data if isinstance(row, dict)]


def looks_scanned(text: str, kind: str, pages: int, source: Path | None = None) -> bool:
    if kind != "pdf":
        return False
    clean = MARKER_RE.sub("", text or "")
    if "(cid:" in clean[:4000] or len(clean.strip()) < 20:
        return True
    meaningful = [line for line in clean.strip().splitlines()
                  if len(line.strip()) >= 8 and not re.match(r"^第\s*\d+\s*[题页]", line.strip())]
    if len(meaningful) >= 3:
        return False
    if source is not None:
        import pymupdf
        with pymupdf.open(source) as document:
            if sum(len(page.get_images(full=True)) for page in document):
                return True
    return len(clean.strip()) < 200 * max(pages, 1)


def render_pages(source: Path, out_dir: Path) -> list[Path]:
    import pymupdf
    paths = []
    with pymupdf.open(source) as document:
        for index, page in enumerate(document, 1):
            target = out_dir / ".pages" / f"page_{index:02d}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            page.get_pixmap(dpi=150).save(target)
            paths.append(target)
    return paths


def vision_page(image: Path, cfg: dict, budget: list[int]) -> list[dict]:
    page_png = image.read_bytes()
    data = base64.b64encode(page_png).decode()
    if "anthropic" in cfg["binding"]:
        content = [{"type": "text", "text": PROMPT},
                   {"type": "image", "media_type": "image/png", "data": data}]
    else:
        content = [{"type": "text", "text": PROMPT},
                   {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}]
    rows = parse_json_array(chat(cfg, content, max_tokens=4000))
    for row in rows:
        figures = []
        for index, box in enumerate(row.get("figure_boxes") or []):
            crop = crop_figure(page_png, box)
            if crop:
                figures.append({"filename": f"figure-{index + 1:02d}.png",
                                "mime_type": "image/png", "data": crop,
                                "_box": list(box) if isinstance(box, (list, tuple)) else []})
        row["images"] = (row.get("images") or []) + figures[:4]
        row.pop("figure_boxes", None)
    if VERIFY_FIGURES != "off":
        for row in rows:
            images = list(row.get("images") or [])
            for index, figure in enumerate(images):
                if not figure.get("_box"):
                    continue
                images[index] = refine_figure(
                    page_png, figure, str(row.get("question") or ""), cfg, budget)
            row["images"] = images
    return rows


def llm_text_questions(text: str, cfg: dict) -> list[dict]:
    chunks, current = [], ""
    for block in re.split(r"\n{2,}", text):
        if current and len(current) + len(block) > 20000:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    chunks.append(current)
    rows = []
    for chunk in chunks:
        rows.extend(parse_json_array(chat(
            cfg, [{"type": "text", "text": PROMPT + "\n\nDOCUMENT TEXT:\n" + chunk}], 4000)))
    return rows


OPTION_RE = re.compile(r"^\s*([A-D])\s*[.、．:：)]\s*(.+?)\s*$", re.M)
ANSWER_RE = re.compile(r"^\s*(?:参考)?答案\s*[:：]?\s*(.+?)\s*$", re.M)
EXPLAIN_RE = re.compile(r"^\s*(?:解析|分析|解答|详解)\s*[:：]?\s*(.+?)\s*$", re.M)
SPLIT_RE = re.compile(r"(?m)^\s*(?:第\s*\d+\s*题[^\n]*|\d{1,3}\s*[.、．]|（\s*\d{1,3}\s*）)\s*")


def heuristic_questions(text: str) -> list[dict]:
    blocks = [b.strip() for b in SPLIT_RE.split(text) if b.strip()] or [text.strip()]
    rows = []
    for block in blocks:
        options = {letter: value.strip() for letter, value in OPTION_RE.findall(block)}
        answer = (ANSWER_RE.search(block) or [None, ""])[1].strip()
        explanation = (EXPLAIN_RE.search(block) or [None, ""])[1].strip()
        body = re.sub(r"\n{2,}", "\n", ANSWER_RE.sub("", EXPLAIN_RE.sub("", OPTION_RE.sub("", block)))).strip()
        if not body:
            continue
        if options:
            question_type = "multi_choice" if re.fullmatch(
                r"[A-D](?:\s*[,，、/]\s*[A-D])+", answer) else "single_choice"
        elif re.search(r"\btrue\b|\bfalse\b|正确|错误", answer, re.I):
            question_type = "true_false"
        else:
            question_type = "short_answer"
        rows.append({"question": re.sub(r"\s*\n\s*", " ", body), "question_type": question_type,
                     "options": options, "correct_answer": answer,
                     "explanation": explanation, "tags": [], "user_answer": ""})
    return rows


def normalize_row(row: dict) -> dict:
    options = row.get("options") or {k: row.get(k) for k in "ABCD" if row.get(k)}
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except ValueError:
            options = {}
    if isinstance(options, list):
        mapped = {}
        for index, item in enumerate(options[:4]):
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("letter") or chr(65 + index)).upper()[:1]
                mapped[label] = item.get("text") or item.get("content") or ""
            else:
                mapped[chr(65 + index)] = str(item)
        options = mapped
    options = {str(k).upper()[:1]: str(v or "").strip() for k, v in options.items()}
    question_type = TYPE_ALIASES.get(str(row.get("question_type") or "").strip().lower(),
                                     str(row.get("question_type") or "").strip().lower())
    if question_type not in TYPES:
        question_type = "single_choice" if len(options) >= 2 else "short_answer"
    answer = row.get("correct_answer")
    if isinstance(answer, list):
        answer = ",".join(str(part) for part in answer)
    if answer is True:
        answer = "true"
    elif answer is False:
        answer = "false"
    answer = str(answer or "").strip()
    if re.fullmatch(r"[A-Da-d](?:\s*[,，、/]\s*[A-Da-d])+", answer):
        answer = ",".join(sorted({part.upper() for part in re.findall(r"[A-Da-d]", answer)}))
    elif re.fullmatch(r"[A-Da-d]", answer):
        answer = answer.upper()
    elif answer.lower() in {"true", "false"}:
        answer = answer.lower()
    tags = row.get("tags")
    if isinstance(tags, list):
        tags = ",".join(str(tag).strip() for tag in tags if str(tag).strip())
    tags = re.sub(r"\s*[,，、]\s*", ",", str(tags or "").strip())
    return {"question": re.sub(r"\s+", " ", str(row.get("question") or "")).strip(),
            "question_type": question_type,
            "A": options.get("A", ""), "B": options.get("B", ""),
            "C": options.get("C", ""), "D": options.get("D", ""),
            "correct_answer": answer,
            "explanation": str(row.get("explanation") or "").strip(),
            "tags": tags,
            "user_answer": str(row.get("user_answer") or "").strip().upper(),
            "images": [image for image in (row.get("images") or [])
                       if isinstance(image, dict) and image.get("data")]}


def validate(rows: list[dict]) -> list[str]:
    warnings = []
    for index, row in enumerate(rows, 2):
        if not row["correct_answer"]:
            warnings.append(f"row {index}: correct_answer is empty")
        if row["question_type"] in {"single_choice", "multi_choice"}:
            available = {key for key in "ABCD" if row[key]}
            letters = set(re.findall(r"[A-D]", row["correct_answer"]))
            if len(available) < 2:
                warnings.append(f"row {index}: choice question has fewer than 2 options")
            if letters and not letters.issubset(available):
                warnings.append(f"row {index}: answer letters {sorted(letters)} have no option")
    return warnings


def build_workbook(rows: list[dict], output: Path, warnings: list[str]) -> dict:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet"
    sheet.append(HEADERS)
    for row in rows:
        sheet.append([row[key] for key in HEADERS])
    side = Side(style="thin", color="D9D9D9")
    border = Border(left=side, right=side, top=side, bottom=side)
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="4472C4")
        cell.font = Font(bold=True, color="FFFFFF", size=11)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    for column, width in {"A": 62, "B": 16, "C": 20, "D": 20, "E": 20, "F": 20,
                          "G": 20, "H": 50, "I": 20, "J": 14}.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2, max_row=max(sheet.max_row, 2)):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(sheet.max_row, 1)}"
    sheet.row_dimensions[1].height = 24
    dropdown = DataValidation(type="list", formula1='"' + ",".join(TYPES) + '"',
                              allow_blank=True, showDropDown=False)
    dropdown.error = "Use one of: " + ", ".join(TYPES)
    sheet.add_data_validation(dropdown)
    dropdown.add(f"B2:B{max(sheet.max_row, 1000)}")
    figures = 0
    if FIGURES != "off":
        import io

        from openpyxl.drawing.image import Image as XLImage

        for excel_row, row in enumerate(rows, 2):
            for index, image in enumerate((row.get("images") or [])[:4]):
                try:
                    picture = XLImage(io.BytesIO(image["data"]))
                except Exception:
                    continue
                if picture.width and picture.width > 240:
                    ratio = 240 / float(picture.width)
                    picture.width = 240
                    picture.height = max(1, int((picture.height or 1) * ratio))
                column = "KLMN"[index]
                sheet.add_image(picture, f"{column}{excel_row}")
                sheet.column_dimensions[column].width = 26
                sheet.row_dimensions[excel_row].height = max(
                    sheet.row_dimensions[excel_row].height or 15,
                    (picture.height or 1) * 0.78,
                )
                figures += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    import zipfile
    with zipfile.ZipFile(output) as package:
        assert package.testzip() is None, "corrupt xlsx zip member"
    return {"rows": len(rows), "figures": figures, "output": str(output), "warnings": warnings}


def load_questions_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("questions") or data.get("rows") or []
    return [row for row in data if isinstance(row, dict)]


def main() -> dict:
    output = resolve(OUTPUT)
    if INPUT_JSON:
        raw_rows, mode = load_questions_json(resolve(INPUT_JSON)), "json"
    else:
        source = resolve(SOURCE)
        if not source.is_file():
            raise FileNotFoundError(f"source not found: {source}")
        text, kind, images = extract_source(source)
        pages = len(text.split("\f"))
        cfg = load_model_config() if USE_LLM != "off" else None
        if looks_scanned(text, kind, pages, source):
            if USE_VISION == "off" or cfg is None:
                raise RuntimeError(
                    "the source has no usable text layer and no vision model is configured. "
                    "Transcribe the pages into a questions JSON file, then rerun with INPUT_JSON set.")
            raw_rows, mode = [], "vision"
            verification_budget = [20]
            for image in render_pages(source, output.parent):
                raw_rows.extend(vision_page(image, cfg, verification_budget))
        elif cfg is not None:
            raw_rows = attach_markers(llm_text_questions(text, cfg), images)
            mode = "llm-text"
        else:
            raw_rows = attach_markers(heuristic_questions(text), images)
            mode = "heuristic"
    rows = [row for row in (normalize_row(raw) for raw in raw_rows) if row["question"]]
    if not rows:
        raise ValueError("no questions were extracted; inspect the source or supply a questions JSON")
    warnings = validate(rows)
    stats = build_workbook(rows, output, warnings)
    audit_rows = [
        {**{key: value for key, value in row.items() if key != "images"},
         "images": [image.get("filename", "") for image in row.get("images") or []]}
        for row in rows
    ]
    audit = output.with_suffix(".questions.json")
    audit.write_text(json.dumps(audit_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    stats.update({"mode": mode, "audit": str(audit)})
    return stats


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False, indent=1))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        sys.exit(1)
# ═══ end converter ═══
```
## Scanned / image-only PDFs

The converter detects a missing text layer, renders pages at 150 dpi, and sends
each page to the active vision model in one call. This needs the model endpoint
to be reachable from the `exec` sandbox. If the call fails or returns
`no usable text layer and no vision model`:

- Do **not** invent questions from the page count.
- Ask the user to attach the page images to the chat, or to paste the text;
  transcribe them into `practice.xlsx.questions.json` using the schema, then
  rerun with `INPUT_JSON`.
- Figures on a scanned page are cropped from the model's `figure_boxes` and
  inserted as floating pictures; ask the model to return `[]` when a question
  has no figure.
- For 作业帮 / 错题本 exports, the printed ✓/√ marks the student's own answer,
  not the correct answer. Put it in `user_answer` and solve `correct_answer`
  independently.

## Figures

- **Text PDFs**: raster images are placed inline by page position and attached
  to the question whose text block contains the marker. Header/footer
  decorations are skipped.
- **DOCX**: inline pictures are attached to the paragraph (question) they sit
  in, including pictures in table cells.
- **Scanned PDFs**: the vision model returns `figure_boxes`; the converter
  crops those regions from the rendered page and attaches them to the row.
- Each question keeps up to 4 figures. Each figure is anchored to `K`, `L`, `M`
  or `N` on its row and scaled to at most 240 px wide; the 10 template columns
  are unchanged, so the practice importer still reads the row correctly.
- `FIGURES = "off"` skips pictures; `PRACTICE_FIGURES` overrides the constant.

- **Verification pass**: every cropped figure is shown beside the full page and
  the question. If the check says the crop is incomplete, the converter may
  re-crop (only a box that contains the original crop) or rasterize a
  model-written SVG, and only keeps the candidate if a second check accepts
  it. Otherwise the original crop is kept. Set `VERIFY_FIGURES = "off"` to
  skip the extra model calls.
- MinerU is **not required**: PyMuPDF provides text and image positions and the
  configured vision model provides OCR/layout for scans. A heavier OCR stack
  only helps stubborn vector-only or multi-column layouts.

## Rules that prevent bad rows

- **Never merge two questions** into one row; split sub-questions that carry
  their own blanks/options, and keep shared context in `question`.
- **Options** stay in C-F; do not leave the letter prefix in the cell.
- **`correct_answer` is not `user_answer`.** A tick, circle, or handwritten
  letter is the student's answer; the correct answer must be solved.
- **`true_false`** stores `true` / `false`, not "正确"/"错误".
- **Multi-select** joins letters with commas (`A,C`), never `AC`.
- **Formulas and units** stay as readable Unicode text; do not emit LaTeX or
  MathML.
- **Tags** name 1-3 knowledge points, comma-separated without spaces.
- If the source prints no answer and the model cannot solve it, leave
  `correct_answer` empty and report the gap instead of guessing.
- Keep the original language of the questions; do not translate unless asked.
