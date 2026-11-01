"""Deep Question Capability.

Routes one user turn through the right quiz-generation path:

* followup — single-call ``FollowupAgent`` reply about one prior question.
* variants  — 举一反三: the learner picked questions, each becomes a template
  whose seed is varied rather than paraphrased; the same pipeline writes,
  validates and streams the new questions.
* custom mode — new ``QuestionPipeline`` (explore → plan → per-question loop).
* mimic mode  — same pipeline, but PDF parsing produces the templates
  and ``templates_override`` skips explore + plan.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import tempfile
from typing import Any

from deeptutor.agents._shared.capability_result import emit_capability_result
from deeptutor.core.capability_protocol import TurnCapability
from deeptutor.core.context import UnifiedContext
from deeptutor.core.trace import merge_trace_metadata
from deeptutor.i18n import StatusI18n
from deeptutor.runtime.agentic.usage import UsageTracker
from deeptutor.runtime.bootstrap.builtin_capabilities import builtin_capability_manifest
from deeptutor.runtime.stream_bus import StreamBus

#: How many seed questions one 举一反三 request may carry. The learner picks a
#: handful of questions; a batch of thirty seeds is a request to generate from
#: the topic, not from those questions.
MAX_SEED_QUESTIONS = 10

#: Ceiling on generated variants, matching ``question_bank.record_set``.
MAX_VARIANT_QUESTIONS = 30

#: The pipeline's own question types; seeds arrive with the bank's names.
_SEED_QUESTION_TYPES = {
    "choice": "choice",
    "single_choice": "choice",
    "multiple_choice": "choice",
    "multi_choice": "choice",
    "mcq": "choice",
    "concept": "concept",
    "true_false": "concept",
    "judgement": "concept",
    "fill_in_blank": "fill_in_blank",
    "fill_blank": "fill_in_blank",
    "cloze": "fill_in_blank",
    "short_answer": "short_answer",
    "written": "written",
    "essay": "written",
    "coding": "coding",
    "code": "coding",
}

_SEED_TOPIC_LIMIT = 60

#: One band up, used by the difficulty ladder of a multi-question batch.
_DIFFICULTY_LADDER = {"easy": "medium", "medium": "hard", "hard": "hard"}


def _variant_difficulty(base: str, index: int, count: int) -> str:
    """Difficulty for variant ``index`` of ``count``.

    A batch that stays flat is a batch the learner can already do; a batch that
    jumps is a wall. The first two thirds stay at the seed's band and the rest
    step up exactly one, so the set ends slightly harder than it started — the
    same ladder the practice-variants skill describes.
    """
    band = (base or "medium").strip().lower()
    if band not in _DIFFICULTY_LADDER:
        band = "medium"
    if count <= 1:
        return band
    two_thirds = max(1, count - (count + 2) // 3)
    return band if index < two_thirds else _DIFFICULTY_LADDER[band]


def _seed_question_type(raw: Any, has_options: bool) -> str:
    """Map a bank question type onto a pipeline type, keeping the shape."""
    key = str(raw or "").strip().lower().replace(" ", "_").replace("-", "_")
    mapped = _SEED_QUESTION_TYPES.get(key)
    if mapped:
        return mapped
    return "choice" if has_options else "short_answer"


def _seed_topic(seed: dict[str, Any]) -> str:
    """What this seed practises; falls back to the stem itself.

    The seed's own topic label (a quiz's ``concentration``) names the batch far
    better than the stem, but a question copied out of the bank may carry none —
    then its opening words are still more useful than "Variant set".
    """
    topic = " ".join(str(seed.get("topic") or "").split())
    if topic:
        return topic[:120]
    stem = " ".join(str(seed.get("question") or "").split())
    return (stem[: _SEED_TOPIC_LIMIT - 1] + "…") if len(stem) > _SEED_TOPIC_LIMIT else stem


def _render_seed_answer(seed: dict[str, Any]) -> str:
    """The seed's answer, shaped so the writer can keep the question type.

    Options are part of the shape, not of the answer: a variant of a
    multiple-choice question has to be multiple choice, and the model can only
    keep that promise if it sees the option set it is varying.
    """
    parts: list[str] = []
    options = seed.get("options")
    if isinstance(options, dict) and options:
        rendered = " | ".join(
            f"{str(key).upper()}. {value}"
            for key, value in options.items()
            if str(value or "").strip()
        )
        if rendered:
            parts.append(f"Options: {rendered}")
    answer = " ".join(str(seed.get("correct_answer") or "").split())
    if answer:
        parts.append(f"Answer: {answer}")
    explanation = " ".join(str(seed.get("explanation") or "").split())
    if explanation:
        parts.append(f"Why: {explanation}")
    return "\n".join(parts)


def _seed_questions(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    """Read ``seed_questions`` from the turn config into usable seeds.

    Accepts both the structured shape the app sends (question, type, options,
    answer…) and a plain list of strings, so an API caller or the CLI can pass
    "these three questions". Anything without a stem is dropped — a variant
    seeded by nothing cannot be written.
    """
    raw = overrides.get("seed_questions")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    seeds: list[dict[str, Any]] = []
    for item in raw[:MAX_SEED_QUESTIONS]:
        if isinstance(item, str):
            question = " ".join(item.split())
            if question:
                # A plain string seed carries no shape metadata; it is still a
                # usable seed, and the writer decides the type it can keep.
                seeds.append(
                    {
                        "question": question,
                        "question_type": "short_answer",
                        "options": {},
                        "correct_answer": "",
                        "explanation": "",
                        "difficulty": "",
                        "topic": "",
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        question = " ".join(str(item.get("question") or "").split())
        if not question:
            continue
        options = item.get("options")
        options = (
            {
                str(key).strip().upper()[:1]: " ".join(str(value).split())
                for key, value in options.items()
                if str(key).strip() and str(value or "").strip()
            }
            if isinstance(options, dict)
            else {}
        )
        seeds.append(
            {
                "question": question,
                "question_type": _seed_question_type(item.get("question_type"), bool(options)),
                "options": options,
                "correct_answer": " ".join(str(item.get("correct_answer") or "").split()),
                "explanation": " ".join(str(item.get("explanation") or "").split()),
                "difficulty": str(item.get("difficulty") or "").strip().lower()[:20],
                "topic": " ".join(
                    str(item.get("topic") or item.get("concentration") or "").split()
                ),
            }
        )
    return seeds


class DeepQuestionCapability(TurnCapability):
    manifest = builtin_capability_manifest("deep_question")

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        from deeptutor.services.llm.config import get_llm_config

        llm_config = get_llm_config()
        kb_name = context.knowledge_bases[0] if context.knowledge_bases else None
        turn_id = str(context.metadata.get("turn_id", "") or context.session_id or "deep-question")
        i18n = StatusI18n(self.name, context.language, module="question")

        overrides = context.config_overrides
        mode = str(overrides.get("mode", "custom") or "custom").strip().lower()
        topic = str(overrides.get("topic") or context.user_message or "").strip()
        # Validate user-fixable input before allocating a workspace or invoking
        # any provider/parser.  This keeps an empty custom request from
        # surfacing a low-level filesystem error such as ``Operation not
        # permitted``.
        if mode == "custom" and not topic:
            await stream.error(
                i18n.t(
                    "topic_required",
                    "Please provide a topic before generating questions.",
                ),
                source=self.name,
                metadata={"code": "topic_required", "retryable": True},
            )
            return
        runtime_workspace = context.runtime.workspace
        if runtime_workspace is not None:
            output_dir = Path(runtime_workspace.output_dir) / "question_generation"
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Direct legacy callers that do not construct a TurnRuntimeContext
            # retain their historical storage path.
            from deeptutor.services.path_service import get_path_service

            output_dir = get_path_service().get_task_workspace("deep_question", turn_id)
        followup_question_context = context.metadata.get("question_followup_context", {}) or {}
        if isinstance(followup_question_context, dict) and followup_question_context.get(
            "question"
        ):
            from deeptutor.agents.question.agents.followup_agent import FollowupAgent

            usage = UsageTracker(model=getattr(llm_config, "model", None))
            agent = FollowupAgent(
                language=context.language,
                api_key=llm_config.api_key,
                base_url=llm_config.base_url,
                api_version=llm_config.api_version,
                token_tracker=usage,
            )
            agent.set_trace_callback(self._build_trace_bridge(stream, i18n=i18n))
            async with stream.stage("generation", source=self.name):
                answer = await agent.process(
                    user_message=context.user_message,
                    question_context=followup_question_context,
                    history_context=str(
                        context.metadata.get("conversation_context_text", "") or ""
                    ).strip(),
                    attachments=context.attachments,
                )
                if answer:
                    await stream.content(answer, source=self.name, stage="generation")
                followup_payload: dict[str, Any] = {
                    "response": answer or "",
                    "mode": "followup",
                    "question_id": followup_question_context.get("question_id", ""),
                }
                await emit_capability_result(
                    stream, followup_payload, source=self.name, usage=usage
                )
            return

        mode = str(overrides.get("mode", "custom") or "custom").strip().lower()
        topic = str(overrides.get("topic") or context.user_message or "").strip()
        num_questions = int(overrides.get("num_questions", 1) or 1)
        difficulty = str(overrides.get("difficulty", "") or "")
        raw_types = overrides.get("question_types") or []
        question_types = list(raw_types) if isinstance(raw_types, list) else []
        raw_counts = overrides.get("per_type_counts") or {}
        per_type_counts = (
            {str(k): int(v) for k, v in raw_counts.items() if isinstance(v, int) and v > 0}
            if isinstance(raw_counts, dict)
            else {}
        )
        history_context = str(context.metadata.get("conversation_context_text", "") or "").strip()

        # 举一反三: the learner picked questions and asked for more like them.
        # Seeds win over the topic path, and a batch that *claims* to be seeded
        # but has no usable stem is an error rather than a silent fallback —
        # generating about the topic instead would answer a different request.
        seeds = _seed_questions(overrides)
        if "seed_questions" in overrides and not seeds:
            await stream.error(
                i18n.t(
                    "variants_invalid_seeds",
                    "The selected questions could not be read; pick them again.",
                ),
                source=self.name,
                metadata={"code": "variants_invalid_seeds", "retryable": True},
            )
            return
        if seeds:
            await self._run_variants(
                context=context,
                stream=stream,
                seeds=seeds,
                topic=topic,
                num_questions=num_questions,
                difficulty=difficulty,
                history_context=history_context,
                kb_name=kb_name,
                i18n=i18n,
            )
            return

        if mode != "mimic":
            # New custom-mode pipeline: explore → plan → per-question quiz loop.
            # The pipeline owns its own stream.content / stream.result emission;
            # nothing here to render afterwards.
            from deeptutor.agents.question.history import load_session_quiz_history
            from deeptutor.agents.question.pipeline import QuestionPipeline
            from deeptutor.agents.question.request_config import (
                build_question_runtime_config,
            )
            from deeptutor.services.config import load_config_with_main

            if not topic:
                await stream.error(
                    i18n.t(
                        "topic_required",
                        "Topic is required for custom question generation.",
                    ),
                    source=self.name,
                )
                return

            quiz_history = await load_session_quiz_history(context.session_id or "")
            runtime_config = build_question_runtime_config(
                base_config=load_config_with_main("main.yaml"),
            )
            pipeline = QuestionPipeline(
                language=context.language,
                kb_name=kb_name,
                enabled_tools=list(context.enabled_tools or []),
                runtime_config=runtime_config,
            )
            await pipeline.run(
                context=context,
                user_message=topic,
                num_questions=num_questions,
                difficulty=difficulty,
                question_types=question_types,
                per_type_counts=per_type_counts,
                conversation_context=history_context,
                attachments=context.attachments,
                quiz_history=quiz_history,
                stream=stream,
            )
            return

        # Mimic mode — also runs through QuestionPipeline, but parses the
        # exam paper into templates first and passes them via
        # ``templates_override`` so explore + plan are skipped.
        await self._run_mimic_mode(
            context=context,
            stream=stream,
            kb_name=kb_name,
            output_dir=output_dir,
            overrides=overrides,
            history_context=history_context,
            num_questions=num_questions,
            i18n=i18n,
        )

    async def _run_variants(
        self,
        *,
        context: UnifiedContext,
        stream: StreamBus,
        seeds: list[dict[str, Any]],
        topic: str,
        num_questions: int,
        difficulty: str,
        history_context: str,
        kb_name: str | None,
        i18n: StatusI18n | None = None,
    ) -> None:
        """举一反三: seed questions → templates → the normal quiz pipeline.

        One generated question per seed; asking for more than there are seeds
        round-robins them, so "再来 5 道类似的" works from a single picked
        question. The pipeline then writes, validates and streams each one
        through exactly the same path as a topic-driven quiz, and the result
        carries ``mode: "variants"`` plus a title the client files the batch
        under.
        """
        from deeptutor.agents.question.history import load_session_quiz_history
        from deeptutor.agents.question.pipeline import QuestionPipeline, QuizTemplate
        from deeptutor.agents.question.request_config import (
            build_question_runtime_config,
        )
        from deeptutor.services.config import load_config_with_main

        if i18n is None:
            i18n = StatusI18n(self.name, context.language, module="question")

        count = min(
            max(int(num_questions or 0), len(seeds)),
            MAX_VARIANT_QUESTIONS,
        )
        templates = []
        for index in range(count):
            seed = seeds[index % len(seeds)]
            templates.append(
                QuizTemplate(
                    question_id=f"q_{index + 1}",
                    topic=_seed_topic(seed),
                    question_type=seed["question_type"],
                    # An explicit request wins; otherwise the batch climbs one
                    # band from the seed's own difficulty.
                    difficulty=(
                        difficulty
                        if difficulty
                        else _variant_difficulty(str(seed.get("difficulty") or ""), index, count)
                    ),
                    source="variant",
                    reference_question=seed["question"],
                    reference_answer=_render_seed_answer(seed),
                )
            )

        await stream.progress(
            message=i18n.t(
                "variants_ready",
                "Generating {count} variant question(s) of the selected questions...",
                count=count,
            ),
            source=self.name,
            stage="generating",
        )

        runtime_config = build_question_runtime_config(
            base_config=load_config_with_main("main.yaml"),
        )
        pipeline = QuestionPipeline(
            language=context.language,
            kb_name=kb_name,
            enabled_tools=list(context.enabled_tools or []),
            runtime_config=runtime_config,
        )
        quiz_history = await load_session_quiz_history(context.session_id or "")
        await pipeline.run(
            context=context,
            user_message=topic,
            num_questions=count,
            difficulty=difficulty,
            conversation_context=history_context,
            attachments=context.attachments,
            quiz_history=quiz_history,
            templates_override=templates,
            stream=stream,
        )

    async def _run_mimic_mode(
        self,
        *,
        context: UnifiedContext,
        stream: StreamBus,
        kb_name: str | None,
        output_dir,
        overrides: dict[str, Any],
        history_context: str,
        num_questions: int,
        i18n: StatusI18n | None = None,
    ) -> None:
        """Resolve an exam paper → templates → ``QuestionPipeline.run`` with
        ``templates_override``. No legacy AgentCoordinator involvement.

        Three input shapes:

        * Uploaded PDF attachment      → write to tmpfile, parse with MinerU
        * Server-side parsed directory → skip parsing, just extract questions
        * ``[Attached Documents]`` in  → no paper available; fall back to
          the user_message text          custom-mode pipeline with a
                                         "mimic the attached source" hint
                                         prefixed onto the user_message
        """
        from deeptutor.agents.question.history import load_session_quiz_history
        from deeptutor.agents.question.mimic_source import (
            parse_exam_paper_to_templates,
        )
        from deeptutor.agents.question.pipeline import QuestionPipeline
        from deeptutor.agents.question.request_config import (
            build_question_runtime_config,
        )
        from deeptutor.services.config import load_config_with_main
        from deeptutor.services.parsing.engines.mineru.config import MinerUError

        if i18n is None:
            i18n = StatusI18n(self.name, context.language, module="question")
        paper_path = str(overrides.get("paper_path", "") or "").strip()
        max_questions = int(overrides.get("max_questions", 10) or 10)
        pdf_attachment = next(
            (
                attachment
                for attachment in context.attachments
                if attachment.filename.lower().endswith(".pdf")
                or attachment.type == "pdf"
                or attachment.mime_type == "application/pdf"
            ),
            None,
        )

        runtime_config = build_question_runtime_config(
            base_config=load_config_with_main("main.yaml"),
        )
        pipeline = QuestionPipeline(
            language=context.language,
            kb_name=kb_name,
            enabled_tools=list(context.enabled_tools or []),
            runtime_config=runtime_config,
        )
        quiz_history = await load_session_quiz_history(context.session_id or "")

        async def _emit_parse_notice(message: str) -> None:
            async with stream.stage("exploring", source=self.name):
                await stream.thinking(message, source=self.name, stage="exploring")

        if pdf_attachment and pdf_attachment.base64:
            # Bridge MinerU's progress lines (emitted from the parser worker
            # thread) back onto the event loop so the trace panel streams them
            # live — model downloads and per-page parsing would otherwise look
            # like a silent multi-minute hang.
            loop = asyncio.get_running_loop()

            def _parse_progress(line: str) -> None:
                asyncio.run_coroutine_threadsafe(
                    stream.thinking(line, source=self.name, stage="exploring"),
                    loop,
                )

            try:
                async with stream.stage("exploring", source=self.name):
                    await stream.thinking(
                        i18n.t(
                            "parsing_uploaded",
                            "Parsing uploaded exam paper and extracting templates...",
                        ),
                        source=self.name,
                        stage="exploring",
                    )
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as temp_pdf:
                        temp_pdf.write(base64.b64decode(pdf_attachment.base64))
                        temp_pdf.flush()
                        templates, _ = await parse_exam_paper_to_templates(
                            temp_pdf.name,
                            max_questions=max_questions,
                            paper_mode="upload",
                            output_dir=output_dir,
                            progress_callback=_parse_progress,
                        )
            except MinerUError as exc:
                await stream.error(str(exc), source=self.name)
                return
            await pipeline.run(
                context=context,
                user_message=context.user_message,
                num_questions=len(templates) or num_questions,
                difficulty="",
                conversation_context=history_context,
                attachments=context.attachments,
                quiz_history=quiz_history,
                templates_override=templates,
                stream=stream,
            )
            return

        if paper_path:
            await _emit_parse_notice(
                i18n.t(
                    "parsing_directory",
                    "Loading parsed exam paper and extracting templates...",
                )
            )
            try:
                templates, _ = await parse_exam_paper_to_templates(
                    paper_path,
                    max_questions=max_questions,
                    paper_mode="parsed",
                    output_dir=output_dir,
                )
            except MinerUError as exc:
                await stream.error(str(exc), source=self.name)
                return
            await pipeline.run(
                context=context,
                user_message=context.user_message,
                num_questions=len(templates) or num_questions,
                difficulty="",
                conversation_context=history_context,
                attachments=context.attachments,
                quiz_history=quiz_history,
                templates_override=templates,
                stream=stream,
            )
            return

        if "[Attached Documents]" in context.user_message:
            # No paper available — degrade to custom-mode generation but
            # bias the pipeline toward shadowing the attached source by
            # prefixing the user message with an explicit instruction.
            mimic_hint = (
                "[Mimic the attached source document as closely as possible: "
                "style, difficulty, structure, and assessed concepts.]\n\n"
            )
            await pipeline.run(
                context=context,
                user_message=mimic_hint + context.user_message,
                num_questions=max_questions,
                difficulty="",
                conversation_context=history_context,
                attachments=context.attachments,
                quiz_history=quiz_history,
                stream=stream,
            )
            return

        await stream.error(
            i18n.t(
                "mimic_needs_paper",
                "Mimic mode requires either an uploaded PDF or a parsed exam directory.",
            ),
            source=self.name,
        )

    def _build_trace_bridge(self, stream: StreamBus, i18n: StatusI18n | None = None):
        async def _trace_bridge(update: dict[str, Any]) -> None:
            event = str(update.get("event", "") or "")
            stage = str(update.get("phase") or update.get("stage") or "generation")
            base_metadata = {
                key: value
                for key, value in update.items()
                if key
                not in {
                    "event",
                    "state",
                    "response",
                    "chunk",
                    "result",
                    "tool_name",
                    "tool_args",
                }
            }

            if event == "llm_call":
                state = str(update.get("state", "running"))
                label = str(update.get("label", "") or "")
                if state == "running":
                    await stream.progress(
                        message=label,
                        source=self.name,
                        stage=stage,
                        metadata=merge_trace_metadata(
                            base_metadata,
                            {"trace_kind": "call_status", "call_state": "running"},
                        ),
                    )
                    return
                if state == "streaming":
                    chunk = str(update.get("chunk", "") or "")
                    if chunk:
                        await stream.thinking(
                            chunk,
                            source=self.name,
                            stage=stage,
                            metadata=merge_trace_metadata(
                                base_metadata,
                                {"trace_kind": "llm_chunk"},
                            ),
                        )
                    return
                if state == "complete":
                    was_streaming = update.get("streaming", False)
                    if not was_streaming:
                        response = str(update.get("response", "") or "")
                        if response:
                            await stream.thinking(
                                response,
                                source=self.name,
                                stage=stage,
                                metadata=merge_trace_metadata(
                                    base_metadata,
                                    {"trace_kind": "llm_output"},
                                ),
                            )
                    await stream.progress(
                        message="",
                        source=self.name,
                        stage=stage,
                        metadata=merge_trace_metadata(
                            base_metadata,
                            {"trace_kind": "call_status", "call_state": "complete"},
                        ),
                    )
                    return
                if state == "error":
                    fallback = (
                        i18n.t("llm_call_failed", "LLM call failed.")
                        if i18n is not None
                        else "LLM call failed."
                    )
                    await stream.error(
                        str(update.get("response", "") or fallback),
                        source=self.name,
                        stage=stage,
                        metadata=merge_trace_metadata(
                            base_metadata,
                            {"trace_kind": "call_status", "call_state": "error"},
                        ),
                    )
                    return

            if event == "tool_call":
                await stream.tool_call(
                    tool_name=str(update.get("tool_name", "") or "tool"),
                    args=update.get("tool_args", {}) or {},
                    source=self.name,
                    stage=stage,
                    metadata=merge_trace_metadata(
                        base_metadata,
                        {"trace_kind": "tool_call"},
                    ),
                )
                return

            if event == "tool_result":
                state = str(update.get("state", "complete"))
                result = str(update.get("result", "") or "")
                if state == "error":
                    await stream.error(
                        result,
                        source=self.name,
                        stage=stage,
                        metadata=merge_trace_metadata(
                            base_metadata,
                            {"trace_kind": "tool_result"},
                        ),
                    )
                    return
                await stream.tool_result(
                    tool_name=str(update.get("tool_name", "") or "tool"),
                    result=result,
                    source=self.name,
                    stage=stage,
                    metadata=merge_trace_metadata(
                        base_metadata,
                        {"trace_kind": "tool_result"},
                    ),
                )

        return _trace_bridge
