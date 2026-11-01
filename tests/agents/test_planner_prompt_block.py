"""The planner block: present when the turn loaded one, absent when it did not."""

from __future__ import annotations

from deeptutor.agents.loop.prompt_blocks import LoopPromptAssembler
from deeptutor.core.context import UnifiedContext

PLANNER_BLOCK = "## 规划上下文\n今天是 2026-11-01 Sat（Asia/Shanghai）。"


def _blocks(context: UnifiedContext):
    assembler = LoopPromptAssembler(prompts={}, language="zh")
    return assembler.blocks(context=context, tool_manifest="")


def _names(context: UnifiedContext) -> list[str]:
    return [block.name for block in _blocks(context)]


def test_planner_block_carries_the_snapshot():
    context = UnifiedContext(planner_context=PLANNER_BLOCK)

    rendered = {block.name: block.content for block in _blocks(context)}

    assert rendered["planner"] == PLANNER_BLOCK


def test_no_snapshot_means_no_empty_block():
    context = UnifiedContext()

    assert "planner" not in _names(context)


def test_planner_sits_after_the_journal_and_before_skills():
    context = UnifiedContext(
        learning_journal_context="journal body",
        planner_context=PLANNER_BLOCK,
        skills_manifest="skills manifest",
        source_manifest="[Task context]…",
    )

    names = _names(context)

    assert names.index("learning_journal") < names.index("planner")
    assert names.index("planner") < names.index("skills")
    assert names.index("planner") < names.index("sources")


def test_field_defaults_to_empty():
    context = UnifiedContext()

    assert context.planner_context == ""
