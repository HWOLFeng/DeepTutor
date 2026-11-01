---
name: practice-variants
description: Generate 举一反三 variant question sets — new questions that practise the same knowledge points as questions the learner picked out of a conversation, quiz card, mistake notebook or question bank — then file them as one named, unanswered practice set the learner can start from the learning sidebar. Use when the learner says 举一反三 / 换几道类似的 / 再练一组 / "more questions like these", or selects seed questions and asks for more.
tags:
- practice
- questions
---

# 举一反三: one named set of variants

The learner points at a few questions, and gets a **new set** that practises the
same knowledge points with different surfaces. The set is a first-class object:
it appears in Learning → Practice, it has a name that says what it practises,
and it is unanswered (a variant is not a mistake until the learner attempts it).

The identity/rendering side is product code; this playbook owns the judgement —
picking seeds, choosing the variant move per question type, keeping the
difficulty ladder honest, naming the set, and reporting back.

## Two ways to run it

- **In the app (preferred when it is available).** The bank's selection bar has
  «相似题» and each quiz card has ✨ «再来几道类似的». Both open the quiz
  composer with the picked questions as seeds; the learner sends and the set is
  filed automatically. Do not re-do that flow in chat — point the learner at
  the button instead.
- **In a conversation (this skill).** When there is no composer to seed — a
  partner/IM turn, a text-only surface, or the learner simply asks in chat —
  generate the questions yourself and file them with
  `question_bank(action="record_set")` (step 6). Use this only when the in-app
  entry cannot do the job, so the same batch does not end up in two places.

## 1. Pin the seeds

Seeds are the questions the learner actually selected or quoted. Read them from
the turn: quiz cards in the conversation, pasted stems, an attached photo, or a
`question_bank` listing they pointed at.

- **1–5 seeds.** 1 seed is fine ("再出一道类似的") — vary it N ways.
- If it is genuinely unclear *which* questions count as seeds (several quiz
  turns in the conversation, an ambiguous "these"), ask **one** short question
  and stop. Do not guess a set on the learner's behalf.
- If a seed has no answer, solve it first. You cannot vary a question you cannot
  answer, and the variant needs a checkable answer.

## 2. Look before writing

`question_bank` with `action="list"` (filter `all`, and `wrong` when the seeds
came from mistakes) shows what the learner already has. Use it to:

- avoid restating a stem that already exists in the bank verbatim;
- see how existing sets are named, so a new name fits the shelf;
- notice when this exact variant batch was already recorded (same name → the
  same set; re-running `record_set` updates it instead of adding a twin).

Skip this step only when the bank is empty — `overview` answers that in one call.

## 3. Choose a variant move per question

Pick **one** move per seed, and keep the question type:

| Seed shape | Move | Do not |
| --- | --- | --- |
| Arithmetic / algebra | change numbers and keep the structure; then reverse it (give the result, ask for the input) | keep the numbers and swap the wording |
| Geometry with a figure | rotate/rename the figure, change one given; keep the same theorem | reuse the same figure with new labels only |
| Word problem | replace the scenario (shopping → sports) and keep the quantities relation | rename the people and change nothing else |
| Multiple choice | rebuild options so distractors come from the same misconception (sign errors, unit slips) | shuffle the seed's options |
| Fill-in-the-blank | blank a different term from the same definition | blank the same word in the same sentence |
| True/false | state the converse, or a near-miss with one wrong quantity | negate the seed's statement word-for-word |
| Reading comprehension | keep the passage-level skill, change the passage | ask about the same sentence |

Hard rules:

1. **Same knowledge point, different surface.** A variant that a learner can
   answer by pattern-matching the seed is not a variant, it is a copy.
2. **Every question has a checkable answer.** Compute it; never write "see the
   solution". If it cannot be graded, it does not belong in the bank.
3. **Explanation says why**, in the learner's language, one or two sentences.

## 4. Keep the difficulty honest

Default ladder over N variants: start at the seeds' difficulty, and move at most
one band up. The app applies exactly this ladder (the last third steps up), so
match it when generating in chat:

- `easy → easy, easy, medium`
- `medium → medium, medium, hard`
- `hard → hard, hard, hard`

A learner who asked for 举一反三 wants fluency, not a wall. If the seeds include
one they got wrong, put that knowledge point **first** — start from what they
must repair — and keep its variant at the same difficulty as the seed.

## 5. Name the set

The name is what the learner sees in the sidebar next to the question count, so
it must say **what the batch practises**:

- good: `二次函数 · 变式 5 题`, `投影的几何意义 · 变式`, `分数四则 · 2026-11-01`
- bad: `举一反三`, `新题`, `练习`, `question set`, `Partner notebook`

Rules:

- Use the seeds' knowledge point / topic wording, in the learner's language.
- Add the count when it helps scanning (`· 变式 5 题`).
- Add a date (`· 2026-11-01`) when this is a repeat of a same-topic batch, so the
  two batches are told apart at a glance. Dates do not replace the topic.
- The name is a label, not an identity: two sets may share a name and stay
  separate rows. Never try to make a name unique by hashing or numbering it
  randomly — say what it practises.

## 6. File it (one call)

```
question_bank(
  action="record_set",
  set="<name from step 5>",
  category="<optional tag>",
  questions=[
    {"question": "...", "question_type": "single_choice",
     "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
     "correct_answer": "B", "explanation": "...", "difficulty": "medium"},
    ...
  ],
)
```

- One call for the whole batch — do not `record` the questions one by one.
- `record_set` saves them **unanswered** (`result=ungraded`), so they do not
  enter the mistake set or the review queue until the learner tries them.
- Its reply reports how many were saved and where they live. If it rejects the
  name, fix the name — do not drop the batch.

## 7. Report back in three lines

Keep the reply short; the questions live in the app, not in the chat:

1. What the set practises and how many variants (one line).
2. Where it is: Learning → Practice (sidebar row), and that it starts from the
   first question.
3. Any deliberate choice worth knowing: "第 2 题难度提高一档，针对上次的符号错误。"

Do **not** paste all the questions into the chat as prose after filing them —
the learner practises them in the app, and duplicated text is confusing to
answer. If the learner explicitly wants to preview them as text, show at most
the first two, then point at the set.

## Failure modes to avoid

- **Seed drift**: generating about the topic in general rather than about the
  selected questions' knowledge point.
- **Clone**: same numbers, same structure, same options — the learner notices.
- **Unanswerable**: an open-ended "discuss" item with no reference answer.
- **Loose rows**: creating questions without `record_set`, so they never become
  a set and the sidebar cannot open them.
- **Mistake pollution**: filing variants as wrong answers (they were never
  attempted) or as `is_correct=true`.
- **Name soup**: three sets called `练习` on the same day.
