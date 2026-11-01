"""Durable identities and names for the practice sets in the question bank.

The learning sidebar lists the bank as *sets*: one imported file, one generated
quiz turn, one partner's recorded mistakes. Every set is something a learner
can walk into and practise question by question, so a row must answer two
questions that are easy to confuse:

* **which entries are in it** — identity, and
* **what to call it** — a name.

Before this module the sidebar grouped rows by ``material_title``, i.e. by the
display name itself. Two sets whose names happened to match (two partners
named alike, a re-import of the same file, every chat-generated quiz, whose
title was empty) collapsed into a single row, and the row could not say which
entries it contained. The name is therefore never the key here: the key is
derived from the columns that actually identify the batch, and the name is a
label computed for display only.

================  ===================================  =====================
kind              key                                   display name
================  ===================================  =====================
``material``      ``material|<material_id>``            ``material_title``
``origin``        ``origin|<origin_ref>``               ``material_title``
``turn``          ``turn|<session_id>|<turn_id>``       session title
``other``         ``other``                             generic label
================  ===================================  =====================

``GROUP_KEY_SQL`` is the single source of truth for that key: the sidebar
groups entries by it and the question-bank listing filters by it, so a row can
never open a different set than the one it counted. Refs handed to the client
carry the :data:`SET_REF_PREFIX`; a legacy ``?group=<material_id>`` link keeps
working because a payload without the prefix is read as a material id.

Only the sources that own a practice set get one. Mastery paths, books and
immersive reading keep their own surfaces (and their own sidebar rows), so
their questions stay in the generic bucket instead of duplicating a topic name
across two kinds of row.
"""

from __future__ import annotations

from typing import Any

#: Sources whose questions form a set the learner practises as a batch.
SET_SOURCES: tuple[str, ...] = ("import", "deep_question", "partner_chat")

#: Prefix on the ref sent to the client. Keeps a new ref distinguishable from
#: the material ids older links embedded directly.
SET_REF_PREFIX = "g:"

#: The set key as SQL. One text, three readers — the sidebar's GROUP BY, the
#: listing's filter and the tests — so a row and its entries cannot drift.
_GROUP_KEY_TEMPLATE = (
    "CASE"
    " WHEN {a}.source NOT IN ('import', 'deep_question', 'partner_chat') THEN 'other'"
    " WHEN COALESCE({a}.material_id, '') != '' THEN 'material|' || {a}.material_id"
    " WHEN {a}.origin_type = 'external_import'"
    " THEN 'origin|' || COALESCE({a}.origin_ref, '')"
    " WHEN COALESCE({a}.session_id, '') != ''"
    " THEN 'turn|' || {a}.session_id || '|' || COALESCE({a}.turn_id, '')"
    " ELSE 'other'"
    " END"
)


def group_key_sql(alias: str = "n") -> str:
    """The grouping expression, qualified with the table ``alias``."""
    if not alias.isidentifier():
        raise ValueError(f"invalid SQL alias: {alias!r}")
    return _GROUP_KEY_TEMPLATE.format(a=alias)


#: The same expression for the question-bank queries, whose table is ``n``.
GROUP_KEY_SQL = group_key_sql("n")

#: A stored session title that carries no information: the store's default for
#: a conversation nobody has named yet (see ``deeptutor.services.session``).
SESSION_TITLE_PLACEHOLDERS = frozenset({"new conversation"})

MAX_SET_TITLE = 120
MAX_SET_REF = 512


def _collapse(value: Any) -> str:
    return " ".join(str(value or "").split())


def set_ref(group_key: str) -> str:
    """The addressable ref for a raw group key."""
    return f"{SET_REF_PREFIX}{group_key}"


def group_key_from_ref(ref: str | None) -> str:
    """Read a client-supplied ref back into the group key it names.

    A ref without :data:`SET_REF_PREFIX` is a legacy ``material_id`` link; an
    empty or over-long value names no set (the caller then filters nothing
    extra rather than guessing).
    """
    value = _collapse(ref)
    if not value or len(value) > MAX_SET_REF:
        return ""
    if value.startswith(SET_REF_PREFIX):
        return value[len(SET_REF_PREFIX) :]
    return f"material|{value}"


def group_filter(group_key: str) -> tuple[str, list[Any]] | None:
    """``(sql, params)`` selecting exactly the entries in one set.

    The SQL is the grouping expression itself rather than a hand-written
    equivalent, so filtered rows and navigated counts are the same set by
    construction.
    """
    key = _collapse(group_key)
    if not key:
        return None
    return f"({GROUP_KEY_SQL}) = ?", [key]


def set_title(
    *,
    material_title: str = "",
    session_title: str = "",
    sample_question: str = "",
) -> str:
    """The name the sidebar shows for one set.

    Precedence is "what the batch is", then "where it was made", then "what is
    in it": a name recorded when the set was created (a file name, a partner's
    name, the topic a quiz was generated for) beats the conversation title,
    which beats the first question's stem. No timestamp is appended — the
    sidebar already shows each row's time, and baking a date into the name
    would rename a set whose entries keep arriving.
    """
    for candidate in (material_title, session_title):
        text = _collapse(candidate)
        if text and text.casefold() not in SESSION_TITLE_PLACEHOLDERS:
            return text[:MAX_SET_TITLE]
    stem = _collapse(sample_question)
    if stem:
        return stem[:MAX_SET_TITLE]
    return ""


__all__ = [
    "GROUP_KEY_SQL",
    "MAX_SET_REF",
    "MAX_SET_TITLE",
    "SESSION_TITLE_PLACEHOLDERS",
    "SET_REF_PREFIX",
    "SET_SOURCES",
    "group_filter",
    "group_key_from_ref",
    "group_key_sql",
    "set_ref",
    "set_title",
]
