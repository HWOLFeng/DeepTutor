"""Deterministic answer checks: code decides what code can decide.

The model is good at transcribing a page and bad at being asked twice. A second
model pass agrees with the first one often enough to be reassuring and wrong
often enough to matter (one cube-net sheet produced four different answers for
the same figure). Where an answer can be *computed*, it should be: a wrong
answer then has a reason, and a right one has a proof.

Each verifier here answers one shape of question:

* arithmetic — evaluate the expression the page printed;
* a chain of equalities — `54-22+8 = 54-30 = 24` is a claim to check, and its
  truth value is the 判断题 answer;
* choice structure — a key that names a missing option is not an answer;
* cube nets — the model reports the printed grid, the cube-rolling solver
  derives the opposite faces.

A verifier returns ``None`` when it does not apply, and a
:class:`Verdict` otherwise. ``unknown`` is a legitimate verdict: it means the
question is not computable, not that it is wrong.

Adding a domain is one function plus one ``register_verifier`` call — the point
of the shape is that a new subject (physics units, chemical equations) plugs in
without touching the importer.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from fractions import Fraction
import inspect
import re
from typing import Any, Callable, Literal

Status = Literal["agree", "disagree", "unknown"]

_MATH_SPAN = re.compile(r"\$([^$]+)\$")
_NUMBER = r"\d+(?:\.\d+)?"
_OPERATOR = r"[+\-×÷*/]"
#: A run of numbers and operators, at least two operands ("25+14-6").
_EXPRESSION = re.compile(rf"(?<![\w.]){_NUMBER}(?:\s*{_OPERATOR}\s*{_NUMBER})+(?![\w.])")
#: An equality chain: "54-22+8 = 54-30 = 24" (spaces optional).
_EQUALS_CHAIN = re.compile(
    rf"{_NUMBER}(?:\s*{_OPERATOR}\s*{_NUMBER})+"
    rf"(?:\s*=\s*{_NUMBER}(?:\s*{_OPERATOR}\s*{_NUMBER})*)+"
)


@dataclass(frozen=True)
class Verdict:
    status: Status
    answer: str = ""
    reason: str = ""

    @property
    def decided(self) -> bool:
        return self.status in {"agree", "disagree"}


def _fraction(value: str) -> Fraction:
    return Fraction(value)


def _eval_node(node: ast.AST) -> Fraction:
    """Evaluate an arithmetic AST without touching ``eval``.

    Only numbers, parentheses, unary minus and ``+ - * / // % **`` are allowed;
    every other node raises, so a stem can never smuggle code into the check.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _eval_node(node.left), _eval_node(node.right)
        match node.op:
            case ast.Add():
                return left + right
            case ast.Sub():
                return left - right
            case ast.Mult():
                return left * right
            case ast.Div():
                return left / right
            case ast.FloorDiv():
                return Fraction(int(left // right))
            case ast.Mod():
                return left % right
            case ast.Pow():
                if abs(right) > 8:
                    raise ValueError("exponent too large")
                return left**int(right)
    raise ValueError(f"unsupported expression: {type(node).__name__}")


def evaluate(expression: str) -> Fraction | None:
    """The exact value of a printed arithmetic expression, or ``None``."""
    text = (
        str(expression or "")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("－", "-")
        .replace("＋", "+")
        .replace("（", "(")
        .replace("）", ")")
        .replace(" ", "")
    )
    if not text or not re.fullmatch(r"[0-9.+\-*/%() ]+", text):
        return None
    try:
        return _eval_node(ast.parse(text, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError):
        return None


def _is_number(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", str(text or "").strip()))


def _stems_math(question: str) -> list[str]:
    """Math spans and bare expressions in the stem, in reading order."""
    spans = [match.group(1) for match in _MATH_SPAN.finditer(question or "")]
    return spans


def _normal(text: str) -> str:
    return str(text or "").strip().replace(" ", "").replace("$", "")


def verify_arithmetic(
    question: str, answer: str, *, options: dict[str, str] | None = None, qtype: str = ""
) -> Verdict | None:
    """Evaluate the printed expression and compare it with the answer."""
    spans = _stems_math(question)
    if not spans:
        return None
    # The last math span is the one the question acts on ("…再计算。$25+14-6$").
    expression = next(
        (span for span in reversed(spans) if _EXPRESSION.fullmatch(span.strip())), ""
    )
    if not expression:
        return None
    value = evaluate(expression)
    if value is None:
        return None
    expected = _normal(answer)
    if not expected:
        return None
    if not _is_number(expected):
        return None
    return Verdict(
        "agree" if Fraction(expected) == value else "disagree",
        answer=str(value),
        reason=f"{expression} = {value}",
    )


def verify_equality_chain(
    question: str, answer: str, *, options: dict[str, str] | None = None, qtype: str = ""
) -> Verdict | None:
    """A printed `a = b = c` claim: every part must evaluate to the same value.

    This is what makes a 判断题 computable: the truth of the statement is not a
    matter of opinion, and the learner's √/× can be graded against it.
    """
    if qtype not in {"true_false"}:
        return None
    match = _EQUALS_CHAIN.search(question or "")
    if not match:
        return None
    parts = [_normal(part) for part in match.group(0).split("=")]
    values = [evaluate(part) for part in parts]
    if any(value is None for value in values):
        return None
    truth = "T" if len(set(values)) == 1 else "F"
    expected = _normal(answer).upper()
    if expected not in {"T", "F", "TRUE", "FALSE", "√", "×", "对", "错"}:
        return None
    normalized = {
        "TRUE": "T",
        "FALSE": "F",
        "√": "T",
        "对": "T",
        "×": "F",
        "错": "F",
    }.get(expected, expected)
    return Verdict(
        "agree" if normalized == truth else "disagree",
        answer=truth,
        reason="; ".join(f"{part} = {value}" for part, value in zip(parts, values)),
    )


def verify_choice(
    question: str, answer: str, *, options: dict[str, str] | None = None, qtype: str = ""
) -> Verdict | None:
    """A choice key must name an option that exists, and be unique when single."""
    if qtype not in {"single_choice", "multi_choice"} or not options:
        return None
    keys = [key.strip().upper() for key in re.split(r"[,;，；\s]+", _normal(answer)) if key.strip()]
    if not keys:
        return None
    if any(key not in {str(option).upper() for option in options} for key in keys):
        return Verdict("disagree", answer="", reason="The answer names a missing option")
    if qtype == "single_choice" and len(set(keys)) != 1:
        return Verdict("disagree", reason="A single-choice question has one answer")
    return Verdict("agree", answer=",".join(sorted(set(keys))), reason="Every key exists")


# ---------------------------------------------------------------------------
# Cube nets: the model reports the printed grid; the solver folds it.
# ---------------------------------------------------------------------------

_IDENTITY = ((1, 0, 0), (0, 1, 0), (0, 0, 1))


def _matmul(a: tuple[tuple[int, int, int], ...], b: tuple[tuple[int, int, int], ...]):
    return tuple(
        tuple(sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3))
        for row in range(3)
    )


def _rot_x(sign: int):
    """Rotation about the page's horizontal axis (moving up/down a cell)."""
    return (
        (1, 0, 0),
        (0, 0, -sign),
        (0, sign, 0),
    )


def _rot_y(sign: int):
    """Rotation about the page's vertical axis (moving left/right a cell)."""
    return (
        (0, 0, sign),
        (0, 1, 0),
        (-sign, 0, 0),
    )


def _normal_of(frame) -> tuple[int, int, int]:
    return tuple(frame[row][2] for row in range(3))  # type: ignore[return-value]


def net_opposites(cells: list[list[Any]]) -> dict[str, str]:
    """Map every label in a folded net to the label on its opposite face.

    ``cells`` is ``[[row, col, label], ...]`` in a printed net; the grid is
    folded by rolling a cube from one square to its neighbours, so the result
    follows the paper's own geometry instead of a memorised net type.
    """
    grid: dict[tuple[int, int], str] = {}
    for cell in cells:
        if not isinstance(cell, (list, tuple)) or len(cell) < 3:
            continue
        try:
            row, col = int(cell[0]), int(cell[1])
        except (TypeError, ValueError):
            continue
        label = str(cell[2]).strip()
        if label:
            grid[(row, col)] = label
    if len(grid) < 2:
        return {}
    start = min(grid)
    frames = {start: _IDENTITY}
    pending = [start]
    while pending:
        cell = pending.pop()
        row, col = cell
        for (d_row, d_col), rotation in (
            ((0, 1), _rot_y(1)),
            ((0, -1), _rot_y(-1)),
            ((1, 0), _rot_x(1)),
            ((-1, 0), _rot_x(-1)),
        ):
            neighbour = (row + d_row, col + d_col)
            if neighbour in grid and neighbour not in frames:
                frames[neighbour] = _matmul(frames[cell], rotation)
                pending.append(neighbour)
    normals = {label: _normal_of(frames[cell]) for cell, label in grid.items()}
    opposites: dict[str, str] = {}
    labels = list(normals)
    for index, first in enumerate(labels):
        for second in labels[index + 1 :]:
            if normals[first] == tuple(-value for value in normals[second]):
                opposites[first] = second
                opposites[second] = first
    return opposites


def _asked_labels(question: str) -> list[str]:
    """The labels a net question asks about, in blank order."""
    return re.findall(r"数字\s*(\d+)\s*的对立面", question or "")


def verify_net(
    question: str, answer: str, *, figure_data: dict[str, Any] | None = None, qtype: str = ""
) -> Verdict | None:
    """Check a cube-net answer against the folded printed grid."""
    if not figure_data or str(figure_data.get("kind") or "") != "net":
        return None
    cells = figure_data.get("cells") or []
    opposites = net_opposites(cells)
    asked = _asked_labels(question)
    parts = [part.strip() for part in re.split(r"[;；,，、]+", _normal(answer)) if part.strip()]
    if not opposites or not asked or not parts:
        return None
    if len(asked) != len(parts):
        return Verdict("disagree", reason="The answer does not cover every blank")
    expected = []
    for label in asked:
        opposite = opposites.get(label)
        if opposite is None:
            return None
        expected.append(opposite)
    agree = [str(int(part)) if _is_number(part) else part for part in parts] == expected
    return Verdict(
        "agree" if agree else "disagree",
        answer="; ".join(expected),
        reason=f"folded net: {opposites}",
    )


Verifier = Callable[..., "Verdict | None"]

#: Ordered: the first verifier that understands the question decides. Domain
#: plugins append here (or use ``register_verifier``) without touching the
#: importer, and a verifier that returns ``None`` simply passes the question on.
VERIFIERS: list[Verifier] = [
    verify_equality_chain,
    verify_net,
    verify_arithmetic,
    verify_choice,
]


def register_verifier(verifier: Verifier, *, first: bool = False) -> None:
    """Add a subject-specific check (units, equations, circuits, …)."""
    if first:
        VERIFIERS.insert(0, verifier)
    else:
        VERIFIERS.append(verifier)


def verify_answer(
    question: str,
    answer: str,
    *,
    options: dict[str, str] | None = None,
    qtype: str = "",
    figure_data: dict[str, Any] | None = None,
) -> Verdict | None:
    """Run the first verifier that applies. ``None`` means "not computable"."""
    for verifier in VERIFIERS:
        # Built-ins and plugins take different optional arguments; pass only the
        # ones the verifier declares, so a new subject can start with two
        # positional parameters and grow later.
        signature = inspect.signature(verifier)
        available = {"options": options, "qtype": qtype, "figure_data": figure_data}
        accepts = {
            name: value
            for name, value in available.items()
            if name in signature.parameters
            or any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in signature.parameters.values()
            )
        }
        try:
            verdict = verifier(question, answer, **accepts)
        except Exception:  # a broken plugin must not block an import
            continue
        if verdict is not None:
            return verdict
    return None


__all__ = [
    "Verdict",
    "VERIFIERS",
    "evaluate",
    "net_opposites",
    "register_verifier",
    "verify_answer",
    "verify_arithmetic",
    "verify_choice",
    "verify_equality_chain",
    "verify_net",
]
