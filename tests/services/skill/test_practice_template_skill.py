"""The builtin practice-template skill: registration and converter behaviour."""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from deeptutor.services.skill.service import BUILTIN_SKILLS_ROOT, SkillService

HEADERS = [
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
]
SKILL_DIR = BUILTIN_SKILLS_ROOT / "practice-template"


def _converter_source() -> str:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    matches = [block for block in blocks if "def build_workbook" in block]
    assert len(matches) == 1, "SKILL.md must hold exactly one runnable converter block"
    return matches[0]


def test_skill_is_registered_and_read_only(tmp_path: Path) -> None:
    service = SkillService(root=tmp_path / "user_skills", builtin_root=BUILTIN_SKILLS_ROOT)
    skill = service.get_detail("practice-template")
    assert skill.source == "builtin"
    assert "PDF" in skill.description and "xlsx" in skill.description
    assert service.read_skill_file(
        "practice-template", "references/practice-schema.md"
    ).startswith("# Practice-template schema reference")
    assert service.summary_entries()


def test_converter_builds_the_template(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    script = tmp_path / "convert.py"
    script.write_text(_converter_source(), encoding="utf-8")
    (tmp_path / "questions.txt").write_text(
        "1. 计算 2+2=？\nA. 3\nB. 4\nC. 5\nD. 6\n答案：B\n解析：2+2=4。\n\n"
        "2. 判断：地球绕太阳转。\n答案：true\n解析：常识。\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "DEEPTUTOR_WORKSPACE_ROOT": str(tmp_path),
        "PRACTICE_SOURCE": "questions.txt",
        "PRACTICE_OUTPUT": "practice.xlsx",
        "PRACTICE_LLM": "off",
    }
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    assert '"rows": 2' in result.stdout

    worksheet = load_workbook(tmp_path / "practice.xlsx")["Sheet"]
    assert [cell.value for cell in worksheet[1]] == HEADERS
    assert worksheet["A2"].value == "计算 2+2=？"
    assert worksheet["B2"].value == "single_choice"
    assert [worksheet["C2"].value, worksheet["D2"].value] == ["3", "4"]
    assert worksheet["G2"].value == "B"
    assert worksheet["H2"].value == "2+2=4。"
    assert worksheet["B3"].value == "true_false"
    assert worksheet["G3"].value == "true"
    assert (tmp_path / "practice.questions.json").is_file()


def test_converter_repairs_from_questions_json(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    import json

    from openpyxl import load_workbook

    script = tmp_path / "convert.py"
    script.write_text(_converter_source(), encoding="utf-8")
    (tmp_path / "rows.json").write_text(
        json.dumps(
            [
                {
                    "question": "Select the prime numbers",
                    "question_type": "multi_choice",
                    "options": {"A": "2", "B": "3", "C": "4"},
                    "correct_answer": ["B", "A"],
                    "explanation": "2 and 3 are prime.",
                    "tags": ["Math", "Primes"],
                    "user_answer": "A",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "DEEPTUTOR_WORKSPACE_ROOT": str(tmp_path),
        "PRACTICE_QUESTIONS_JSON": "rows.json",
        "PRACTICE_OUTPUT": "repaired.xlsx",
    }
    subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    worksheet = load_workbook(tmp_path / "repaired.xlsx")["Sheet"]
    assert worksheet["B2"].value == "multi_choice"
    assert worksheet["G2"].value == "A,B"
    assert worksheet["I2"].value == "Math,Primes"
    assert worksheet["J2"].value == "A"


def test_converter_adds_floating_figures(tmp_path: Path) -> None:
    pytest.importorskip("openpyxl")
    pymupdf = pytest.importorskip("pymupdf")
    image_module = pytest.importorskip("PIL.Image")
    draw_module = pytest.importorskip("PIL.ImageDraw")
    from openpyxl import load_workbook

    figure = image_module.new("RGB", (320, 160), "white")
    pen = draw_module.Draw(figure)
    pen.rectangle([10, 10, 310, 150], outline="black", width=3)
    pen.line([(10, 80), (310, 80)], fill="black", width=2)
    buffer = io.BytesIO()
    figure.save(buffer, "PNG")
    png = buffer.getvalue()

    document = pymupdf.open()
    page = document.new_page()
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
    (tmp_path / "figure.pdf").write_bytes(document.tobytes())
    document.close()

    script = tmp_path / "convert.py"
    script.write_text(_converter_source(), encoding="utf-8")
    env = {
        **os.environ,
        "DEEPTUTOR_WORKSPACE_ROOT": str(tmp_path),
        "PRACTICE_SOURCE": "figure.pdf",
        "PRACTICE_OUTPUT": "figure.xlsx",
        "PRACTICE_LLM": "off",
    }
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    assert '"figures": 1' in result.stdout
    worksheet = load_workbook(tmp_path / "figure.xlsx")["Sheet"]
    assert len(worksheet._images) == 1
    anchor = worksheet._images[0].anchor._from
    assert anchor.row + 1 == 3  # the second question's row
    assert anchor.col == 10  # column K


def test_converter_can_rasterize_a_redrawn_svg(tmp_path: Path) -> None:
    pytest.importorskip("pymupdf")
    namespace: dict = {}
    exec(compile(_converter_source(), "<converter>", "exec"), namespace)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect x="10" y="10" width="180" height="80" fill="none" stroke="black"/>'
        '<text x="30" y="60" font-size="16">10cm</text></svg>'
    )
    png = namespace["_svg_png"](svg)
    assert png is not None and png.startswith(b"\x89PNG")
    assert namespace["_svg_png"]("<script>alert(1)</script>") is None
