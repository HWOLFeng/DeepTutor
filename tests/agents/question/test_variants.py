"""举一反三: seed questions become templates the pipeline varies.

Regression cover for the product rule behind the feature: a variant batch is
generated *from the questions the learner picked*, not from a topic and not by
copying the seed. These tests pin the seed parsing (what the app sends is not
always clean), the template shape the pipeline relies on, and the envelope the
client files the batch under.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from deeptutor.agents.question.capability import (
    MAX_SEED_QUESTIONS,
    DeepQuestionCapability,
    _render_seed_answer,
    _seed_question_type,
    _seed_questions,
    _seed_topic,
    _variant_difficulty,
)
from deeptutor.agents.question.pipeline import QuestionPipeline, QuizTemplate
from deeptutor.core.context import UnifiedContext


def _seed(**overrides: Any) -> dict[str, Any]:
    seed = {
        "question": "Solve 3x + 5 = 20.",
        "question_type": "single_choice",
        "options": {"A": "5", "B": "15", "C": "8", "D": "25"},
        "correct_answer": "A",
        "explanation": "3x = 15, so x = 5.",
        "difficulty": "easy",
        "topic": "一元一次方程",
    }
    seed.update(overrides)
    return seed


# ── seed parsing ───────────────────────────────────────────────────────


def test_seed_questions_accept_the_app_shape_and_plain_strings() -> None:
    seeds = _seed_questions(
        {"seed_questions": [_seed(), "  What is 7 × 8?  ", {"question": "   "}, 42]}
    )

    assert [seed["question"] for seed in seeds] == ["Solve 3x + 5 = 20.", "What is 7 × 8?"]
    # Type, options, answer and topic survive; stray fields do not.
    assert seeds[0]["question_type"] == "choice"
    assert seeds[0]["options"] == {"A": "5", "B": "15", "C": "8", "D": "25"}
    assert seeds[0]["correct_answer"] == "A"
    assert seeds[0]["topic"] == "一元一次方程"
    # A string seed has no shape metadata; it is still usable.
    assert seeds[1]["question_type"] == "short_answer"
    assert seeds[1]["options"] == {}


def test_seed_questions_are_capped_and_absent_seeds_read_as_empty() -> None:
    many = [f"question {index}" for index in range(MAX_SEED_QUESTIONS + 5)]

    assert len(_seed_questions({"seed_questions": many})) == MAX_SEED_QUESTIONS
    assert _seed_questions({}) == []
    assert _seed_questions({"seed_questions": "not a list"}) == []


@pytest.mark.parametrize(
    ("raw", "has_options", "expected"),
    [
        ("multi_choice", True, "choice"),
        ("single_choice", True, "choice"),
        ("true_false", False, "concept"),
        ("fill_blank", False, "fill_in_blank"),
        ("short-answer", False, "short_answer"),
        ("essay", False, "written"),
        ("code", False, "coding"),
        # Unknown shapes fall back to what the question actually looks like.
        ("mystery", True, "choice"),
        ("mystery", False, "short_answer"),
    ],
)
def test_seed_question_types_map_onto_pipeline_types(
    raw: str, has_options: bool, expected: str
) -> None:
    assert _seed_question_type(raw, has_options) == expected


def test_a_seed_topic_falls_back_to_its_stem() -> None:
    assert _seed_topic({"topic": "一元一次方程"}) == "一元一次方程"
    stem = "x" * 100
    topic = _seed_topic({"question": stem})
    assert len(topic) == 60
    assert topic.endswith("…")


def test_a_seed_answer_carries_the_shape_the_writer_must_keep() -> None:
    rendered = _render_seed_answer(_seed())

    assert "Options: A. 5 | B. 15 | C. 8 | D. 25" in rendered
    assert "Answer: A" in rendered
    assert "Why: 3x = 15, so x = 5." in rendered
    # A seed without an answer still renders something usable.
    assert _render_seed_answer({"question": "Q?"}) == ""


# ── templates + routing ────────────────────────────────────────────────


class _StubBus:
    """A StreamBus stand-in; the event lists are kept distinct from the method
    names, because an instance attribute shadows the method (and then
    ``bus.progress(...)`` raises 'list object is not callable')."""

    def __init__(self) -> None:
        self.progress_events: list[dict[str, Any]] = []

    async def progress(self, message: str, **kwargs: Any) -> None:
        self.progress_events.append({"message": message, **kwargs})


class _StubI18n:
    def t(self, key: str, default: str = "", /, **kwargs: Any) -> str:
        return default.format(**kwargs) if kwargs else default


def _capture_variant_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch the pipeline + history loader so ``_run_variants`` needs no model."""
    captured: dict[str, Any] = {}

    class FakePipeline:
        def __init__(self, **kwargs: Any) -> None:
            captured["pipeline_kwargs"] = kwargs

        async def run(self, **kwargs: Any) -> None:
            captured["run_kwargs"] = kwargs

    async def _no_history(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        "deeptutor.agents.question.pipeline.QuestionPipeline", FakePipeline
    )
    monkeypatch.setattr(
        "deeptutor.agents.question.history.load_session_quiz_history", _no_history
    )
    monkeypatch.setattr(
        "deeptutor.services.config.load_config_with_main", lambda _name: {}
    )
    return captured


def test_variants_build_one_template_per_question_and_vary_the_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_variant_run(monkeypatch)
    bus = _StubBus()
    context = UnifiedContext(user_message="再来几道", session_id="s1", language="zh")

    asyncio.run(
        DeepQuestionCapability()._run_variants(
            context=context,
            stream=bus,  # type: ignore[arg-type]
            seeds=_seed_questions({"seed_questions": [_seed()]}),
            topic="再来几道",
            num_questions=2,
            difficulty="",
            history_context="",
            kb_name=None,
            i18n=_StubI18n(),  # type: ignore[arg-type]
        )
    )

    templates: list[QuizTemplate] = captured["run_kwargs"]["templates_override"]
    assert [template.question_id for template in templates] == ["q_1", "q_2"]
    # One seed, two variants: the seed is reused rather than dropped.
    assert {template.source for template in templates} == {"variant"}
    assert {template.reference_question for template in templates} == {
        "Solve 3x + 5 = 20."
    }
    assert templates[0].topic == "一元一次方程"
    assert templates[0].difficulty == "easy"
    assert "Answer: A" in (templates[0].reference_answer or "")
    assert captured["run_kwargs"]["num_questions"] == 2
    # The learner sees what is happening; the batch is not a silent rewrite.
    assert bus.progress_events and "2" in bus.progress_events[0]["message"]


def test_variants_round_robin_seeds_when_more_questions_are_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_variant_run(monkeypatch)
    seeds = _seed_questions(
        {
            "seed_questions": [
                _seed(question="Q1", topic="topic one"),
                _seed(question="Q2", topic="topic two"),
            ]
        }
    )

    asyncio.run(
        DeepQuestionCapability()._run_variants(
            context=UnifiedContext(user_message="5 more", session_id="s1"),
            stream=_StubBus(),  # type: ignore[arg-type]
            seeds=seeds,
            topic="5 more",
            num_questions=5,
            difficulty="",
            history_context="",
            kb_name=None,
            i18n=_StubI18n(),  # type: ignore[arg-type]
        )
    )

    templates = captured["run_kwargs"]["templates_override"]
    assert [template.reference_question for template in templates] == [
        "Q1",
        "Q2",
        "Q1",
        "Q2",
        "Q1",
    ]


def test_a_batch_climbs_one_difficulty_band_and_stops_there() -> None:
    # easy + 3 → the first two stay easy, the last steps up once.
    assert [_variant_difficulty("easy", i, 3) for i in range(3)] == [
        "easy",
        "easy",
        "medium",
    ]
    assert [_variant_difficulty("medium", i, 2) for i in range(2)] == [
        "medium",
        "hard",
    ]
    # hard is the ceiling: the ladder must not invent a fourth band.
    assert [_variant_difficulty("hard", i, 4) for i in range(4)] == ["hard"] * 4
    # A single variant has no ladder to climb.
    assert [_variant_difficulty("easy", 0, 1)] == ["easy"]
    # Unknown / missing bands fall back to medium rather than leaking through.
    assert _variant_difficulty("", 1, 2) == "hard"
    assert _variant_difficulty("expert", 0, 1) == "medium"


def test_variants_apply_the_difficulty_ladder_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_variant_run(monkeypatch)

    asyncio.run(
        DeepQuestionCapability()._run_variants(
            context=UnifiedContext(user_message="more", session_id="s1"),
            stream=_StubBus(),  # type: ignore[arg-type]
            seeds=_seed_questions({"seed_questions": [_seed(difficulty="easy")]}),
            topic="more",
            num_questions=3,
            difficulty="",
            history_context="",
            kb_name=None,
            i18n=_StubI18n(),  # type: ignore[arg-type]
        )
    )

    difficulties = [
        template.difficulty for template in captured["run_kwargs"]["templates_override"]
    ]
    assert difficulties == ["easy", "easy", "medium"]


def test_variants_respect_the_requested_difficulty(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_variant_run(monkeypatch)

    asyncio.run(
        DeepQuestionCapability()._run_variants(
            context=UnifiedContext(user_message="harder", session_id="s1"),
            stream=_StubBus(),  # type: ignore[arg-type]
            seeds=_seed_questions({"seed_questions": [_seed()]}),
            topic="harder",
            num_questions=1,
            difficulty="hard",
            history_context="",
            kb_name=None,
            i18n=_StubI18n(),  # type: ignore[arg-type]
        )
    )

    assert captured["run_kwargs"]["templates_override"][0].difficulty == "hard"


@pytest.mark.asyncio
async def test_run_routes_seeded_requests_to_variants_and_rejects_junk_seeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "deeptutor.services.llm.config.get_llm_config",
        lambda: SimpleNamespace(model="m", api_key="k", binding="openai", base_url=""),
    )
    calls: list[dict[str, Any]] = []

    async def _fake_variants(_self: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(DeepQuestionCapability, "_run_variants", _fake_variants)

    capability = DeepQuestionCapability()
    bus = _StubBus()
    bus.error = lambda *args, **kwargs: None  # type: ignore[attr-defined]

    await capability.run(
        UnifiedContext(
            user_message="再来几道",
            session_id="s1",
            config_overrides={"seed_questions": [_seed()]},
        ),
        bus,  # type: ignore[arg-type]
    )
    assert calls and calls[0]["num_questions"] == 1

    # A request that claims seeds but carries none is an error, not a silent
    # topic-generated quiz — the learner asked about *those* questions.
    errors: list[str] = []

    async def _error(message: str, **_kwargs: Any) -> None:
        errors.append(message)

    bus.error = _error  # type: ignore[attr-defined]
    await capability.run(
        UnifiedContext(
            user_message="再来几道",
            session_id="s1",
            config_overrides={"seed_questions": [{"question": "   "}]},
        ),
        bus,  # type: ignore[arg-type]
    )
    assert calls and len(calls) == 1
    assert errors and "pick them again" in errors[0]


# ── pipeline: the variant template + envelope ──────────────────────────


def test_variant_templates_get_a_vary_instruction_not_a_mimic_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "deeptutor.agents.question.pipeline.get_llm_config",
        lambda: SimpleNamespace(model="m", api_key="k"),
    )
    pipeline = QuestionPipeline(language="en")
    template = QuizTemplate(
        question_id="q_1",
        topic="equations",
        question_type="choice",
        difficulty="medium",
        source="variant",
        reference_question="Solve 3x + 5 = 20.",
        reference_answer="Options: A. 5 | B. 15\nAnswer: A",
    )

    block = pipeline._render_reference_block(template)

    assert "Solve 3x + 5 = 20." in block
    assert "Answer: A" in block
    # The writer is told to change the surface, and never to copy the seed.
    assert "Never reuse the seed" in block
    assert "Reference question" not in block


def test_variants_envelope_names_the_batch_and_marks_its_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "deeptutor.agents.question.pipeline.get_llm_config",
        lambda: SimpleNamespace(model="m", api_key="k"),
    )
    from deeptutor.agents.question.pipeline import QuizPair, QuizPlan

    pipeline = QuestionPipeline(language="en")
    template = QuizTemplate(
        question_id="q_1",
        topic="linear equations",
        question_type="choice",
        difficulty="medium",
        source="variant",
        reference_question="Solve 3x + 5 = 20.",
    )
    qa = QuizPair(
        question_id="q_1",
        question="Solve 4x - 7 = 21.",
        question_type="choice",
        correct_answer="B",
        explanation="…",
        options={"A": "3", "B": "7"},
        topic="linear equations",
        difficulty="medium",
    )

    payload = pipeline._build_result_payload(
        QuizPlan(analysis="", templates=[template]),
        [qa],
        is_mimic=True,
        is_variants=True,
    )

    assert payload["mode"] == "variants"
    assert payload["summary"]["source"] == "variants"
    assert payload["summary"]["templates"][0]["source"] == "variant"
    # The client files this batch under the title, so it must say "variants".
    assert payload["summary"]["title"].endswith("variants")
    assert "linear equations" in payload["summary"]["title"]


def test_variant_title_falls_back_when_there_are_no_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "deeptutor.agents.question.pipeline.get_llm_config",
        lambda: SimpleNamespace(model="m", api_key="k"),
    )
    pipeline = QuestionPipeline(language="zh")
    from deeptutor.agents.question.pipeline import QuizPlan

    title = pipeline._set_title(
        QuizPlan(analysis="", templates=[]), is_variants=True
    )
    assert title  # never empty: the sidebar needs a label

    assert "变式" in QuestionPipeline(language="zh")._set_title(
        QuizPlan(
            analysis="",
            templates=[
                QuizTemplate(
                    question_id="q_1",
                    topic="一元一次方程",
                    question_type="choice",
                    difficulty="easy",
                )
            ],
        ),
        is_variants=True,
    )
