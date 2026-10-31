"""Deterministic verification: arithmetic, statements, choices, cube nets.

The model transcribes; these functions decide. A verifier that cannot decide
must say so — "unknown" is not a failure, it is what keeps a guess from
becoming a grade.
"""

from __future__ import annotations

import pytest

from deeptutor.services.practice.verify import (
    Verdict,
    evaluate,
    net_opposites,
    register_verifier,
    verify_answer,
    verify_arithmetic,
    verify_choice,
    verify_equality_chain,
    verify_net,
)

#: The classic 1-4-1 net: one square on top, a row of four, one below.
NET_1_4_1 = [[0, 1, "1"], [1, 0, "2"], [1, 1, "4"], [1, 2, "5"], [1, 3, "6"], [2, 1, "3"]]
#: A 2-3-1 shape: a pair on top, a row of three, one square below.
NET_2_3_1 = [[0, 0, "2"], [0, 1, "1"], [1, 1, "3"], [1, 2, "4"], [1, 3, "5"], [2, 1, "6"]]


@pytest.mark.parametrize(
    ("expression", "value"),
    [("25+14-6", 33), ("38-12+24", 50), ("8×9÷2", 36), ("(3+4)*2", 14), ("54-30", 24)],
)
def test_arithmetic_evaluates_exactly(expression, value):
    assert evaluate(expression) == value


@pytest.mark.parametrize("expression", ["", "abc", "__import__('os')", "1/0"])
def test_arithmetic_refuses_what_it_cannot_evaluate(expression):
    assert evaluate(expression) is None


def test_an_expression_in_the_stem_confirms_a_right_answer():
    verdict = verify_arithmetic("用“○”画出先算什么，再计算。$25+14-6$", "33")
    assert verdict == Verdict("agree", answer="33", reason="25+14-6 = 33")


def test_a_wrong_number_is_caught_with_the_reason():
    verdict = verify_arithmetic("先计算 $25+14-6$", "31")
    assert verdict is not None and verdict.status == "disagree"
    assert verdict.answer == "33"


def test_a_false_equality_chain_is_a_false_statement():
    verdict = verify_equality_chain("$54-22+8 = 54-30 = 24$", "T", qtype="true_false")
    assert verdict is not None and verdict.status == "disagree"
    assert verdict.answer == "F"


def test_a_true_equality_chain_agrees_with_true():
    verdict = verify_equality_chain("$54-22+8 = 32+8 = 40$", "T", qtype="true_false")
    assert verdict is not None and verdict.status == "agree"


def test_an_equality_chain_only_speaks_for_true_false_questions():
    assert verify_equality_chain("$54-22+8 = 54-30 = 24$", "24", qtype="short_answer") is None


def test_a_choice_answer_must_name_an_existing_option():
    verdict = verify_choice("哪一组？", "E", options={"A": "1", "B": "2"}, qtype="single_choice")
    assert verdict is not None and verdict.status == "disagree"


def test_a_single_choice_with_two_keys_is_not_an_answer():
    verdict = verify_choice(
        "哪一组？", "A,B", options={"A": "1", "B": "2"}, qtype="single_choice"
    )
    assert verdict is not None and verdict.status == "disagree"


def test_a_choice_key_that_exists_agrees():
    verdict = verify_choice("哪一组？", "b", options={"A": "1", "B": "2"}, qtype="single_choice")
    assert verdict is not None and verdict.status == "agree"


def test_the_folded_net_gives_the_opposite_faces():
    assert net_opposites(NET_1_4_1) == {
        "1": "3",
        "3": "1",
        "2": "5",
        "5": "2",
        "4": "6",
        "6": "4",
    }


def test_a_staircase_net_is_folded_not_memorised():
    # 2-3-1: the column 1-3-6 is a strip, so its two ends face each other.
    opposite = net_opposites(NET_2_3_1)
    assert opposite["1"] == "6"
    assert opposite["6"] == "1"


def test_a_net_answer_is_checked_against_the_grid():
    data = {"kind": "net", "cells": NET_1_4_1}
    good = verify_net("数字1的对立面是（ ）；数字2的对立面是（ ）", "3；5", figure_data=data)
    assert good is not None and good.status == "agree"
    bad = verify_net("数字1的对立面是（ ）；数字2的对立面是（ ）", "3；6", figure_data=data)
    assert bad is not None and bad.status == "disagree"
    assert bad.answer == "3; 5"


def test_a_net_without_grid_data_is_not_guessed():
    assert verify_net("数字1的对立面是（ ）", "3", figure_data=None) is None


def test_an_uncovered_blank_is_reported_rather_than_graded():
    data = {"kind": "net", "cells": NET_1_4_1}
    verdict = verify_net("数字1的对立面是（ ）；数字2的对立面是（ ）", "3", figure_data=data)
    assert verdict is not None and verdict.status == "disagree"


def test_dispatch_returns_none_for_a_question_nobody_can_check():
    assert verify_answer("概括这篇文章的中心思想。", "表达了思乡之情。") is None


def test_a_subject_plugin_can_join_the_dispatch():
    def always_agree(question: str, answer: str) -> Verdict | None:
        if "电压" not in question:
            return None
        return Verdict("agree", answer=answer, reason="plugin")

    register_verifier(always_agree)
    verdict = verify_answer("这个电路的电压是多少？", "3V")
    assert verdict is not None and verdict.reason == "plugin"
