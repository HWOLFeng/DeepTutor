"""Practice sets: one durable identity and one honest name per batch.

Regression cover for two reported failures of the learning sidebar:

* two sets whose names matched collapsed into one row, and a partner's set was
  always called "Partner notebook" — a name used as a key;
* clicking a set generated in a conversation did nothing, because its row
  carried no set to open (unlike an imported file, which did).

These tests pin the contract the sidebar depends on: a row counts a set, its
ref selects exactly those entries, and its name is a label that may repeat.
"""

from __future__ import annotations

import asyncio

import pytest

from deeptutor.services.session.notebook_sets import (
    calibration_payload,
    group_key_from_ref,
    set_ref,
    set_title,
)
from deeptutor.services.session.sqlite_store import SQLiteSessionStore


@pytest.fixture
def store(tmp_path) -> SQLiteSessionStore:
    return SQLiteSessionStore(db_path=tmp_path / "sets.db")


def _quiz(store: SQLiteSessionStore, session_id: str, turn: str, count: int, **extra):
    return asyncio.run(
        store.upsert_notebook_entries(
            session_id,
            [
                {
                    "turn_id": turn,
                    "question_id": f"{turn}_q{index}",
                    "question": f"question {index} of {turn}",
                    "correct_answer": "A",
                    "is_correct": False,
                    **extra,
                }
                for index in range(count)
            ],
        )
    )


def _rows(store: SQLiteSessionStore) -> dict[str, dict]:
    return {row["group_ref"]: row for row in asyncio.run(store.list_notebook_navigation())}


def test_two_quizzes_in_one_conversation_are_two_sets(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session(title="Vectors"))
    _quiz(store, session["id"], "turn_a", 3)
    _quiz(store, session["id"], "turn_b", 2)

    rows = list(_rows(store).values())
    assert len(rows) == 2
    # Same conversation, so the same label — the identity keeps them apart.
    assert {row["title"] for row in rows} == {"Vectors"}
    assert {row["count"] for row in rows} == {2, 3}


def test_a_set_name_is_not_its_key(store: SQLiteSessionStore) -> None:
    first = asyncio.run(store.create_session(title="Drill"))
    second = asyncio.run(store.create_session(title="Drill"))
    _quiz(store, first["id"], "turn_a", 1)
    _quiz(store, second["id"], "turn_b", 1)

    rows = list(_rows(store).values())
    assert len(rows) == 2
    assert [row["title"] for row in rows] == ["Drill", "Drill"]


def test_a_named_material_set_is_named_and_grouped_by_its_material(
    store: SQLiteSessionStore,
) -> None:
    session = asyncio.run(store.create_session(title="Vectors"))
    _quiz(store, session["id"], "turn_a", 2, material_id="quiz:turn_a", material_title="投影 · 变式")

    row = next(iter(_rows(store).values()))
    assert row["title"] == "投影 · 变式"
    assert row["group_ref"] == "g:material|quiz:turn_a"
    assert row["count"] == 2


def test_two_partner_pairings_keep_their_own_rows_and_readable_names(
    store: SQLiteSessionStore,
) -> None:
    for partner, actor in (("p1", "learner-a"), ("p2", "learner-b")):
        asyncio.run(
            store.upsert_notebook_entries(
                None,
                [
                    {
                        "origin_type": "external_import",
                        "origin_ref": f"partner:{partner}:{actor}",
                        "material_id": f"partner:{partner}:{actor}",
                        "material_title": "Study Buddy",
                        "question_id": f"{partner}_q1",
                        "question": "7 × 8?",
                        "source": "partner_chat",
                        "is_correct": False,
                    }
                ],
            )
        )

    rows = list(_rows(store).values())
    assert len(rows) == 2
    assert {row["title"] for row in rows} == {"Study Buddy"}
    assert {row["group_ref"] for row in rows} == {
        "g:material|partner:p1:learner-a",
        "g:material|partner:p2:learner-b",
    }


def test_surfaces_with_their_own_sidebar_row_stay_in_one_generic_bucket(
    store: SQLiteSessionStore,
) -> None:
    session = asyncio.run(store.create_session(title="Vectors"))
    _quiz(store, session["id"], "turn_a", 1, source="mastery_path", material_id="path-1")
    _quiz(store, session["id"], "turn_b", 1, source="book")

    rows = _rows(store)
    # Mastery paths, books and reading keep their own surfaces; their questions
    # are not practice sets, so they share the one generic bucket — which is
    # labelled generically rather than after one arbitrary member.
    assert set(rows) == {set_ref("other")}
    assert rows[set_ref("other")]["title"] == ""
    assert rows[set_ref("other")]["count"] == 2


def test_group_ref_round_trips_through_the_listing(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session(title="Vectors"))
    _quiz(store, session["id"], "turn_a", 3)
    _quiz(store, session["id"], "turn_b", 2, material_id="quiz:turn_b", material_title="投影")
    asyncio.run(
        store.upsert_notebook_entries(
            None,
            [
                {
                    "origin_type": "external_import",
                    "origin_ref": "practice-import",
                    "material_id": "import:tok",
                    "material_title": "lesson.csv",
                    "question_id": "f1",
                    "question": "imported?",
                    "source": "import",
                }
            ],
        )
    )

    for row in _rows(store).values():
        listing = asyncio.run(
            store.list_notebook_entries(group_ref=row["group_ref"], limit=200)
        )
        assert listing["total"] == row["count"], row["group_ref"]


def test_a_legacy_material_link_still_opens_its_set(store: SQLiteSessionStore) -> None:
    asyncio.run(
        store.upsert_notebook_entries(
            None,
            [
                {
                    "origin_type": "external_import",
                    "origin_ref": "practice-import",
                    "material_id": "import:tok",
                    "material_title": "lesson.csv",
                    "question_id": "f1",
                    "question": "imported?",
                    "source": "import",
                }
            ],
        )
    )

    listing = asyncio.run(store.list_notebook_entries(group_ref="import:tok"))
    assert listing["total"] == 1
    assert group_key_from_ref("import:tok") == "material|import:tok"
    assert group_key_from_ref("") == ""
    assert group_key_from_ref("g:other") == "other"


def test_unknown_or_over_long_refs_select_nothing_rather_than_everything(
    store: SQLiteSessionStore,
) -> None:
    _quiz(store, asyncio.run(store.create_session(title="Vectors"))["id"], "turn_a", 2)

    assert asyncio.run(store.list_notebook_entries(group_ref="g:material|nope"))["total"] == 0
    # A ref the sidebar could never mint is not silently dropped: dropping it
    # would open the whole bank and look like a working link.
    assert asyncio.run(store.list_notebook_entries(group_ref="x" * 600))["total"] == 0


def test_set_title_prefers_provenance_then_context_then_content() -> None:
    assert set_title(material_title="lesson.csv", session_title="Chat") == "lesson.csv"
    assert set_title(session_title="投影的几何意义") == "投影的几何意义"
    assert set_title(session_title="New conversation", sample_question="求投影？") == "求投影？"
    assert set_title(sample_question="a" * 200).endswith("a" * 120)
    assert set_title() == ""


def test_calibration_reads_the_attempts_without_inventing_evidence() -> None:
    untouched = calibration_payload(
        group_ref="g:material|quiz:t1", title="Variants", total=5, answered=0, correct=0
    )
    assert untouched["label"] == "untouched"
    assert untouched["suggested_difficulty"] == ""
    assert untouched["accuracy"] == 0.0

    failing = calibration_payload(
        group_ref="g:material|quiz:t2", title="Variants", total=5, answered=4, correct=1
    )
    assert failing["label"] == "too_hard"
    assert failing["accuracy"] == 0.25
    # A set the learner keeps failing should produce an easier batch next time.
    assert failing["suggested_difficulty"] == "easy"

    easy = calibration_payload(
        group_ref="g:material|quiz:t3", title="Variants", total=5, answered=5, correct=5
    )
    assert easy["label"] == "too_easy"
    assert easy["suggested_difficulty"] == "hard"

    on_track = calibration_payload(
        group_ref="g:material|quiz:t4", title="Variants", total=5, answered=5, correct=3
    )
    assert on_track["label"] == "on_track"
    assert on_track["suggested_difficulty"] == ""
    # Correct can never exceed answered, whatever a caller passes.
    assert calibration_payload(
        group_ref="g:x", title="", total=1, answered=1, correct=9
    )["correct"] == 1


def _attempted(store: SQLiteSessionStore, material_id: str, correct: int, wrong: int) -> None:
    items = [
        {
            "origin_type": "external_import",
            "origin_ref": "practice-import",
            "material_id": material_id,
            "material_title": material_id,
            "question_id": f"{material_id}-c{index}",
            "question": f"correct {index} of {material_id}",
            "source": "import",
            "result": "correct",
            "is_correct": True,
            "user_answer": "A",
        }
        for index in range(correct)
    ] + [
        {
            "origin_type": "external_import",
            "origin_ref": "practice-import",
            "material_id": material_id,
            "material_title": material_id,
            "question_id": f"{material_id}-w{index}",
            "question": f"wrong {index} of {material_id}",
            "source": "import",
            "result": "incorrect",
            "is_correct": False,
            "user_answer": "B",
        }
        for index in range(wrong)
    ]
    asyncio.run(store.upsert_notebook_entries(None, items))


def test_a_set_calibration_counts_only_attempted_questions(store: SQLiteSessionStore) -> None:
    _attempted(store, "import:hard", correct=1, wrong=3)
    # An untouched question in the same set is not a failure.
    asyncio.run(
        store.upsert_notebook_entries(
            None,
            [
                {
                    "origin_type": "external_import",
                    "origin_ref": "practice-import",
                    "material_id": "import:hard",
                    "material_title": "hard",
                    "question_id": "import:hard-untouched",
                    "question": "untouched",
                    "source": "import",
                    "result": "ungraded",
                    "is_correct": False,
                }
            ],
        )
    )

    calibration = asyncio.run(store.notebook_set_calibration("g:material|import:hard"))

    assert calibration is not None
    assert calibration["count"] == 5
    assert calibration["answered"] == 4
    assert calibration["correct"] == 1
    assert calibration["accuracy"] == 0.25
    assert calibration["label"] == "too_hard"


def test_sets_needing_practice_ignore_unmeasured_sets_and_rank_worst_first(
    store: SQLiteSessionStore,
) -> None:
    _attempted(store, "import:worst", correct=0, wrong=4)
    _attempted(store, "import:bad", correct=1, wrong=3)
    _attempted(store, "import:fine", correct=4, wrong=1)
    _attempted(store, "import:tiny", correct=0, wrong=2)  # too few attempts

    attention = asyncio.run(store.notebook_sets_needing_practice(limit=3))

    assert [item["title"] for item in attention] == ["import:worst", "import:bad"]
    assert attention[0]["accuracy"] < attention[1]["accuracy"]
    assert asyncio.run(store.notebook_set_calibration("g:material|import:nope")) is None
