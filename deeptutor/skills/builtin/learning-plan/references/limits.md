# Why a plan came out the way it did

Everything here is decided by `deeptutor/services/planner/planner.py`. This page
exists so you can **explain** a plan without re-deriving or contradicting it.

## Durations are not yours to set

The generator assigns every row a length from a table keyed by row kind, and the
`items` you pass only carry a title, an optional date, and an optional kind. Pass
no duration — and do not promise minutes in prose either. If the learner asks
"how long is this block", read the number off the draft; if they want a different
length, the card's own edit control is the right answer.

## The daily ceiling limits the calendar, not the to-do list

A day has a capacity for **scheduled blocks**. When the week is full:

- the task still appears in the draft — it is still work the learner has;
- its time block is not placed, and the tool says how many tasks that happened to;
- if the learner wants more calendar time than the ceiling allows, the honest
  answer is that the ceiling is a setting, not an obstacle to route around.

Never "solve" a full week by stacking two blocks on the same slot.

## Where a block lands

The due day first; failing that, the latest earlier day that still has room —
closest to the deadline. **Never after the due date**: a time block that arrives
after the work was due is worse than no block, because it looks like a plan.

Blocks are also placed inside the learner's study window and around what is
already on the calendar, and each one keeps a gap from the next.

## Duplicates and overlaps mean different things

- **Duplicate** — the same title on the same date as a card already on the
  board. Accepting it merges into that card rather than adding a second one.
  Say "this one will join the task you already have", not "this is a new task".
- **Overlap** — the block collides with an existing event. It is still offered,
  flagged, because only the learner knows whether that collision matters.

## Reviews

Due spaced-repetition cards are folded into a single locked row: the workload,
not one row per card. Locked means accepted by default and impossible to refuse
— skipping a due review only sends it back to tomorrow's queue. Moving it is
allowed, and that is the correct suggestion when a day is too full.

## When there is nothing to plan

If the learner has no due reviews and you proposed no items, `plan_week` refuses
rather than producing an empty draft. That is not a failure to report as an
error: ask what they want the week to be about.

## Numbers you may quote

Only these, and only after a tool returns them: the row counts the tool reports,
the minutes each row carries, the free-time total in the draft summary, and how
many reviews are due. Anything else — "about an hour a day", "you'll finish in
two weeks" — is a guess wearing a number's clothes.
