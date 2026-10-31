"""The deterministic half of import quality: answers, blanks, duplicates.

Every rule here exists because the model got it wrong at least once on a real
photo: a learner's "×" was compared with the key's "F", a filled blank was
transcribed into the stem, and one cube-net page produced two different answers
for the same figure.
"""

from __future__ import annotations

import pytest

from deeptutor.services.practice.quality import (
    answer_in_question,
    answer_keys,
    classify_attempt,
    normalize_answer,
    question_fingerprint,
    strip_answer_leak,
)


@pytest.mark.parametrize("written", ["×", "✗", "错", "错误", "F", "false"])
def test_a_wrong_mark_and_its_words_are_the_same_answer(written):
    assert normalize_answer(written, "true_false") == "F"


@pytest.mark.parametrize("written", ["√", "对", "正确", "T", "true"])
def test_a_right_mark_and_its_words_are_the_same_answer(written):
    assert normalize_answer(written, "true_false") == "T"


def test_choice_answers_become_sorted_keys():
    assert normalize_answer("c、a", "multi_choice", {"A": "1", "B": "2", "C": "3"}) == "A,C"
    assert normalize_answer("B", "single_choice", {"A": "1", "B": "2"}) == "B"


def test_an_option_body_names_its_key():
    assert answer_keys("4", "single_choice", {"A": "3", "B": "4"}) == {"B"}


def test_free_text_answers_ignore_case_spacing_and_trailing_punctuation():
    assert normalize_answer("  62 本。") == normalize_answer("62本")


def test_a_written_mark_grades_as_the_true_false_key():
    assert classify_attempt("×", "F", "true_false") == "correct"


def test_a_learner_who_answered_the_multi_choice_right_is_correct():
    options = {"A": "2", "B": "3", "C": "4", "D": "6"}
    assert classify_attempt("B,D", "B,D", "multi_choice", options) == "correct"
    assert classify_attempt("B,C", "B,D", "multi_choice", options) == "incorrect"


def test_a_final_number_matching_the_last_step_is_correct():
    assert (
        classify_attempt("33", "先算25+14=39，再算39-6=33", "short_answer") == "correct"
    )


def test_a_written_solution_is_not_compared_as_a_string():
    """Different spellings of the same work must not become a fake mistake."""
    assert (
        classify_attempt(
            "46+44-37=90-37=53", "46+44-37=53", "short_answer"
        )
        == "unknown"
    )


def test_an_empty_answer_is_unknown_never_incorrect():
    assert classify_attempt("", "B", "single_choice", {"A": "1", "B": "2"}) == "unknown"
    assert classify_attempt("B", "", "single_choice", {"A": "1", "B": "2"}) == "unknown"


def test_a_filled_blank_is_restored_and_reported():
    question = "秋天是 liáng shuǎng（凉爽）的，被秋风吹红的 shuāng yè（霜叶）有的落在地上。"
    cleaned, removed = strip_answer_leak(question, ["凉爽；霜叶"])
    assert removed == ["凉爽", "霜叶"]
    assert "凉爽" not in cleaned and "霜叶" not in cleaned
    assert cleaned.count("（　　）") == 2


def test_an_underlined_blank_is_restored_too():
    cleaned, removed = strip_answer_leak("它把黄色给了____银杏树____，红色给了____枫树____。", ["银杏树；枫树"])
    assert removed == ["银杏树", "枫树"]
    assert "银杏树" not in cleaned


def test_a_question_that_merely_mentions_a_word_keeps_it():
    """Only blanks are rewritten; prose is left alone even if it looks like an answer."""
    question = "《枫桥夜泊》的作者是谁？"
    cleaned, removed = strip_answer_leak(question, ["张继"])
    assert cleaned == question and removed == []


def test_leak_detection_covers_the_filled_blank():
    assert answer_in_question("秋天是（凉爽）的", "凉爽", "凉爽")
    assert not answer_in_question("秋天是什么季节？（　　）", "秋天", "")


def test_two_stems_that_differ_only_in_blank_width_are_the_same_question():
    assert question_fingerprint("数字1的对立面是（ ）") == question_fingerprint(
        "数字1的对立面是(　　)。"
    )
    assert question_fingerprint("数字1的对立面是（ ）") != question_fingerprint(
        "数字2的对立面是（ ）"
    )


def test_latex_markup_is_presentation_not_answer_content():
    assert (
        classify_attempt("90; 53; 46+44-37=53", "90; 53; $46+44-37=53$", "short_answer")
        == "correct"
    )


def test_a_written_computation_that_ends_in_the_key_number_is_correct():
    assert classify_attempt("85-48+25=62（本）", "62本", "short_answer") == "correct"
    assert classify_attempt("28+4-3=29（元）", "29元", "short_answer") == "correct"


def test_an_expression_without_a_result_stays_unknown():
    assert classify_attempt("85-48+25", "62本", "short_answer") == "unknown"


def test_a_leading_sub_question_label_is_not_a_blank():
    """「（2）」 numbers a sub-question; it must not become a blank."""
    question = "（2）看着月光下格外平静的洞庭湖"
    cleaned, removed = strip_answer_leak(question, ["2"])
    assert cleaned == question and removed == []
