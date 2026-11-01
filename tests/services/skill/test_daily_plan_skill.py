"""The builtin daily-plan skill: registration, and the traps it must name."""

from __future__ import annotations

from pathlib import Path

from deeptutor.services.skill.service import BUILTIN_SKILLS_ROOT, SkillService

SKILL = "daily-plan"
SKILL_DIR = BUILTIN_SKILLS_ROOT / SKILL


def _service(tmp_path: Path) -> SkillService:
    return SkillService(root=tmp_path / "user_skills", builtin_root=BUILTIN_SKILLS_ROOT)


def _body(tmp_path: Path) -> str:
    return _service(tmp_path).read_skill_file(SKILL)


def test_it_loads_as_a_read_only_builtin(tmp_path: Path) -> None:
    detail = _service(tmp_path).get_detail(SKILL)

    assert detail.source == "builtin"
    assert not getattr(detail, "always", False)
    assert "planner" in (detail.tags or [])


def test_the_description_states_when_to_reach_for_it(tmp_path: Path) -> None:
    description = _service(tmp_path).get_detail(SKILL).description.lower()

    for phrase in ("each day", "reminded", "automate", "what should i do today"):
        assert phrase in description, phrase


def test_it_drives_the_existing_cron_tool_rather_than_new_machinery(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert "cron" in body
    # The three actions that matter, named: look first, schedule, cancel.
    assert 'action="list"' in body
    assert "cron_expr" in body
    assert 'action="cancel"' in body


def test_the_message_it_writes_has_to_be_self_contained(tmp_path: Path) -> None:
    body = _body(tmp_path)

    # The job starts a fresh turn that cannot see the conversation that made it.
    assert "self-contained" in body.lower() or "cannot see this conversation" in body
    assert "draft" in body.lower()


def test_it_tells_the_learner_the_consequences_and_the_failure_rule(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert "calendar" in body.lower()
    assert "cancel" in body.lower()
    # A reminder that silently failed is discovered at seven in the morning.
    assert "do not describe a reminder as" in body.lower()


def test_it_forbids_a_second_job(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert "one job per request" in body.lower()
    assert "do not add a" in body.lower()


def test_its_walkthrough_covers_all_seven_steps(tmp_path: Path) -> None:
    e2e = _service(tmp_path).read_skill_file(SKILL, "references/e2e.md")

    for step in range(1, 8):
        assert f"| {step} |" in e2e, step
    # And it names the two silent failures the walkthrough exists to catch.
    assert "self-contained message" in e2e.lower()
    assert "the draft, not the write" in e2e.lower()


def test_the_skill_package_holds_only_what_the_model_needs() -> None:
    entries = {path.name for path in SKILL_DIR.iterdir()}

    assert entries == {"SKILL.md", "references"}
