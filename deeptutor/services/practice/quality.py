"""Deterministic quality rules for imported questions.

The vision model is probabilistic: it transcribes handwriting into the stem,
answers a cube-net question differently on two pages, and prints "F" where the
learner wrote "×". Those failures are cheaper to catch with rules than with
another model call, so this module holds every judgement that must not depend
on a model:

* :func:`normalize_answer` — one canonical form for the learner's answer and
  the correct answer, so "×", "错" and "false" are the same answer.
* :func:`classify_attempt` — did the learner actually answer this correctly?
  ``unknown`` is a real outcome; it must never be silently graded wrong.
* :func:`strip_answer_leak` — a printed blank the learner filled in by hand
  ("（凉爽）") is restored to "（　　）" and reported, so the question stops
  containing its own answer.
* :func:`question_fingerprint` — duplicate detection that ignores blanks.

Nothing here raises for malformed input: an import must not die because one
row has a stray character.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
import unicodedata

#: Answer words that mean "true" / "false" in a 判断题, including the marks a
#: learner writes in the bracket.
_TRUE_WORDS = {"t", "true", "1", "y", "yes", "对", "正确", "√", "✓", "是", "v"}
_FALSE_WORDS = {"f", "false", "0", "n", "no", "错", "错误", "×", "✗", "x", "否", "╳"}
_TRUE_MARK = "T"
_FALSE_MARK = "F"

#: A printed blank, in the shapes Chinese worksheets actually use.
_BLANK_PATTERNS = (
    re.compile(r"（\s*([^（）]{1,24}?)\s*）"),
    re.compile(r"\(\s*([^()]{1,24}?)\s*\)"),
    re.compile(r"_{2,}\s*([^_\s]{1,24}?)\s*_{2,}"),
    re.compile(r"＿{2,}\s*([^＿\s]{1,24}?)\s*＿{2,}"),
)
#: Substrings that are punctuation, not an answer.
_PUNCT_ONLY = re.compile(r"^[\s，。、；：？！,.;:?!\-—…·（）()【】\[\]\"'“”‘’]+$")
_OPTION_LETTERS = re.compile(r"[A-J]")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_SEPARATORS = re.compile(r"[;；,，、/|]+")
#: The first decisive true/false mark in an answer that also carries working
#: ("×，订正：54-22+8=40"). Longest words first so "正确" wins over "对".
_TRUE_FALSE_MARK = re.compile(r"正确|错误|√|✓|×|✗|╳|对|错|true|false|(?<![a-z])t(?![a-z])|(?<![a-z])f(?![a-z])")


def fold(value: Any) -> str:
    """NFKC + collapsed whitespace + casefold. The base of every comparison."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("（", "(").replace("）", ")")
    return " ".join(text.split()).casefold()


def _letters(value: str) -> list[str]:
    """Option keys written in a cell, in order, deduplicated.

    Handles "A", "a,c", "A、C", "AC", "(B)" — and refuses to invent keys from
    words that merely contain a letter.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).upper()
    compact = re.sub(r"[\s,，、;；/|.。()（）\[\]【】]+", "", text)
    if not compact or not re.fullmatch(r"[A-J]+", compact):
        return []
    return list(dict.fromkeys(compact))


def normalize_answer(
    value: Any,
    question_type: str = "",
    options: dict[str, str] | None = None,
) -> str:
    """The canonical form of one answer.

    Choice answers become sorted option keys ("A,C"); 判断题 answers become
    "T"/"F"; everything else keeps its text with punctuation and spacing
    removed, because a learner typing "62 本" and a key saying "62本" are the
    same answer.
    """
    options = options or {}
    kind = str(question_type or "").strip().lower()
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not raw:
        return ""
    folded = fold(raw)
    if kind == "true_false" or folded in _TRUE_WORDS | _FALSE_WORDS:
        word = folded.strip(" ()[]【】")
        if word in _TRUE_WORDS:
            return _TRUE_MARK
        if word in _FALSE_WORDS:
            return _FALSE_MARK
        # The learner wrote the mark and then their correction ("×，订正：…").
        # The mark is the answer; the working is not.
        mark = _TRUE_FALSE_MARK.search(folded)
        if mark:
            return _TRUE_MARK if mark.group(0) in _TRUE_WORDS else _FALSE_MARK
        return folded
    if kind in {"single_choice", "multi_choice", "choice", "multiple_select"} or options:
        keys = _letters(raw)
        if not keys:
            # The answer may quote an option body instead of its letter.
            for key, body in options.items():
                if folded and folded == fold(body):
                    keys = [str(key).upper()]
                    break
        if not keys:
            # "D（增加）" and "选D" carry the key plus commentary.
            usable = {str(key).upper() for key in options} if options else set("ABCD")
            for match in re.finditer(r"(?<![A-Za-z])([A-J])(?![A-Za-z])", raw.upper()):
                if match.group(1) in usable:
                    keys = [match.group(1)]
                    break
        if keys:
            return ",".join(sorted(dict.fromkeys(keys)))
    # Free text: drop separators at the edges, keep the words. LaTeX markup is
    # presentation, not answer content: "$46+44-37=53$" and "46+44-37=53" are
    # the same answer.
    text = raw.replace("$", "")
    text = (
        text.replace("\\div", "÷")
        .replace("\\times", "×")
        .replace("\\cdot", "·")
        .replace("\\%", "%")
    )
    text = re.sub(r"^[\s:：=]+", "", text)
    text = re.sub(r"[\s。．.;；,，]+$", "", text)
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def answer_keys(
    value: Any,
    question_type: str = "",
    options: dict[str, str] | None = None,
) -> set[str] | None:
    """Option keys named by an answer, or ``None`` when it names none."""
    options = options or {}
    kind = str(question_type or "").strip().lower()
    if kind in {"true_false"}:
        normalized = normalize_answer(value, kind)
        return {normalized} if normalized in {_TRUE_MARK, _FALSE_MARK} else None
    keys = _letters(value)
    if keys and (not options or set(keys) <= {str(key).upper() for key in options}):
        return set(keys)
    if keys:
        return None
    for key, body in options.items():
        if fold(value) and fold(value) == fold(body):
            return {str(key).upper()}
    return None


def _answer_parts(value: Any) -> list[str]:
    """Split a multi-blank answer ("3; 6; 5") into comparable pieces."""
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in _SEPARATORS.split(text) if part.strip()]


def _trailing_number(value: str) -> float | None:
    """The last number in an answer, prose or not ("…再算39-6=33" → 33)."""
    found = _NUMBER.findall(unicodedata.normalize("NFKC", value))
    return float(found[-1]) if found else None


def _looks_like_computation(value: str) -> bool:
    return bool(re.search(r"[=＝+\-×÷*/]", value))


def _numbers(value: str) -> list[float] | None:
    """The numbers in a short numeric answer, or ``None`` if it is prose."""
    text = unicodedata.normalize("NFKC", value).strip()
    if not text:
        return None
    stripped = _NUMBER.sub("", text)
    # Allow units and separators around the numbers, nothing else.
    if re.search(r"[A-Za-z\u4e00-\u9fff]{2,}", stripped):
        return None
    found = _NUMBER.findall(text)
    return [float(number) for number in found] if found else None


def classify_attempt(
    user_answer: Any,
    correct_answer: Any,
    question_type: str = "",
    options: dict[str, str] | None = None,
) -> str:
    """``"correct"``, ``"incorrect"`` or ``"unknown"``.

    ``unknown`` is deliberate: a written solution ("46+44-37=53") and a final
    number ("33") describe the same work but are not comparable strings, and
    guessing wrong here turns a correct answer into a fake mistake.
    """
    user = normalize_answer(user_answer, question_type, options)
    correct = normalize_answer(correct_answer, question_type, options)
    if not user or not correct:
        return "unknown"
    if user == correct:
        return "correct"
    if question_type in {"single_choice", "multi_choice", "true_false", "choice"}:
        return "incorrect"
    user_parts, correct_parts = _answer_parts(user), _answer_parts(correct)
    if len(user_parts) > 1 and len(user_parts) == len(correct_parts):
        return "correct" if user_parts == correct_parts else "unknown"
    user_numbers, correct_numbers = _numbers(user), _numbers(correct)
    if user_numbers is not None:
        # A written solution may hold intermediate numbers; only its last one
        # is the answer. A mismatch is only "incorrect" when both sides are a
        # single number — otherwise the learner may simply have stopped at an
        # earlier blank.
        if len(user_numbers) == 1:
            trailing = _trailing_number(correct)
            if trailing is not None and trailing == user_numbers[0]:
                if correct_numbers or _looks_like_computation(correct):
                    return "correct"
            if correct_numbers and len(correct_numbers) == 1:
                return "incorrect"
            return "unknown"
        # The learner wrote the whole computation ("85-48+25=62（本）") while the
        # key holds just the result ("62本"); the last number is the answer.
        if correct_numbers and len(correct_numbers) == 1 and _looks_like_computation(user):
            if _trailing_number(user) == correct_numbers[0]:
                return "correct"
        return "unknown"
    if len(user) >= 4 and len(correct) >= 4 and (user in correct or correct in user):
        # A written solution that contains the expected answer verbatim.
        return "correct"
    return "unknown"


def _is_answer_like(text: str, candidates: set[str]) -> bool:
    folded = fold(text).strip("()（）")
    if not folded or _PUNCT_ONLY.match(folded):
        return False
    if len(folded) > 12:
        return False
    return folded in candidates


def strip_answer_leak(
    question: str,
    answers: list[str],
    *,
    question_type: str = "",
    options: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Restore blanks the learner filled in by hand.

    "（凉爽）" becomes "（　　）" and the removed text is returned, because a
    question that already contains its answer cannot be practised. Only
    bracket/underline blanks are rewritten; an answer that sits in plain
    prose is left for :func:`answer_in_question` to flag, since deleting it
    could damage a question that legitimately quotes it.
    """
    candidates = {
        fold(part).strip("()（）")
        for answer in answers
        for part in _answer_parts(answer)
        if fold(part).strip("()（）")
    }
    if not candidates or not question:
        return question, []

    removed: list[str] = []
    text = question

    def replace(match: re.Match[str]) -> str:
        content = match.group(1)
        # "（2）" at the very start is a sub-question label, not a filled blank —
        # stripping it would renumber the worksheet.
        if match.start() == 0:
            return match.group(0)
        if not _is_answer_like(content, candidates):
            return match.group(0)
        removed.append(content.strip())
        opening = match.group(0)[0]
        if opening in "（(":
            return "（　　）"
        return "＿＿＿＿"

    for pattern in _BLANK_PATTERNS:
        text = pattern.sub(replace, text)
    return text, removed


def answer_in_question(
    question: str,
    correct_answer: Any,
    user_answer: Any,
    question_type: str = "",
    options: dict[str, str] | None = None,
) -> bool:
    """Whether the question still carries the answer it is asking for."""
    _, removed = strip_answer_leak(
        str(question or ""),
        [str(correct_answer or ""), str(user_answer or "")],
        question_type=question_type,
        options=options,
    )
    if removed:
        return True
    # A short answer sitting in the prose next to a filled blank. Only length
    # >= 2 is considered: "对/错" appear inside ordinary questions.
    folded_question = fold(question)
    for part in _answer_parts(correct_answer):
        needle = fold(part)
        if len(needle) < 2 or _PUNCT_ONLY.match(needle):
            continue
        if needle and needle in folded_question and fold(user_answer) == needle:
            return True
    return False


def question_fingerprint(question: str) -> str:
    """A stem identity that ignores blanks, spacing and punctuation."""
    text = fold(question)
    for pattern in _BLANK_PATTERNS:
        text = pattern.sub("( )", text)
    text = re.sub(r"[()\s，。、；：？！,.;:?!\-—…·\"'“”‘’]+", "", text)
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


__all__ = [
    "answer_in_question",
    "answer_keys",
    "classify_attempt",
    "fold",
    "normalize_answer",
    "question_fingerprint",
    "strip_answer_leak",
]
