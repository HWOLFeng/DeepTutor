"""The builtin learning-plan skill: registration and the rules it must carry."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from deeptutor.services.skill.service import BUILTIN_SKILLS_ROOT, SkillService

SKILL = "learning-plan"
SKILL_DIR = BUILTIN_SKILLS_ROOT / SKILL

#: A plan's numbers live in `services/planner/planner.py`. A playbook that
#: repeats them becomes a second source of truth the moment either changes.
CODE_CONSTANT = re.compile(r"\b\d{1,3}\s*(?:min|minutes|mins)\b", re.IGNORECASE)


def _service(tmp_path: Path) -> SkillService:
    return SkillService(root=tmp_path / "user_skills", builtin_root=BUILTIN_SKILLS_ROOT)


def _body(tmp_path: Path) -> str:
    return _service(tmp_path).read_skill_file(SKILL)


def test_it_loads_as_a_read_only_builtin(tmp_path: Path) -> None:
    service = _service(tmp_path)
    detail = service.get_detail(SKILL)

    assert detail.source == "builtin"
    assert detail.name == SKILL
    # Not eager: planning is not on every turn, and an always-on playbook would
    # tax every unrelated conversation.
    assert not getattr(detail, "always", False)
    assert "planner" in (detail.tags or [])


def test_the_description_states_when_to_reach_for_it(tmp_path: Path) -> None:
    description = _service(tmp_path).get_detail(SKILL).description

    for phrase in ("plan", "what should I do today", "排计划", "overdue"):
        assert phrase in description, phrase


def test_it_names_the_draft_tool_and_forbids_writing_the_plan(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert "plan_week" in body
    assert "Never call an apply/confirm endpoint" in body
    # The prohibition has to be about the learner's decision, not just style.
    assert "the learner's act" in body


def test_it_keeps_the_three_intents_apart(tmp_path: Path) -> None:
    body = _body(tmp_path)

    for intent in ("Generate", "Converge", "Repair"):
        assert intent in body, intent
    assert "Query only" in body


def test_it_does_not_restate_the_code_s_constants(tmp_path: Path) -> None:
    body = _body(tmp_path)

    leaked = CODE_CONSTANT.findall(body)
    assert not leaked, f"playbook repeats durations that live in code: {leaked}"


def test_it_stays_within_the_documented_length(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert len(body.splitlines()) <= 150


def test_its_two_references_are_readable(tmp_path: Path) -> None:
    service = _service(tmp_path)

    limits = service.read_skill_file(SKILL, "references/limits.md")
    examples = service.read_skill_file(SKILL, "references/examples.md")

    assert limits.startswith("# Why a plan came out the way it did")
    assert "daily ceiling" in limits
    for scenario in ("Generate", "Converge", "Repair"):
        assert scenario in examples


def test_the_references_may_explain_but_not_renumber(tmp_path: Path) -> None:
    service = _service(tmp_path)

    for name in ("references/limits.md", "references/examples.md"):
        text = service.read_skill_file(SKILL, name)
        assert not CODE_CONSTANT.findall(text), name


def test_it_is_listed_once(tmp_path: Path) -> None:
    names = [skill.name for skill in _service(tmp_path).list_skills()]

    assert names.count(SKILL) == 1


def test_writing_into_the_builtin_skill_is_refused(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(Exception):
        service.delete_skill(SKILL)


def test_the_skill_directory_holds_only_what_the_model_needs() -> None:
    entries = {path.name for path in SKILL_DIR.iterdir()}

    # No README, changelog or setup guide: a skill package carries the playbook
    # and the documents it links to, nothing else.
    assert entries == {"SKILL.md", "references"}
