---
name: learning-plan
description: Turn a study goal into a reviewable plan of tasks and calendar time blocks, repair a plan that slipped, and answer "what should I do today" — always through plan_week and the learner's own row-by-row acceptance. Use whenever the user asks to plan a week, 排计划, 安排复习, catch up on overdue work, or asks what to study next.
tags:
  - planner
---

# Learning plan

A plan is not something you hand over finished. It is a proposal the learner
accepts row by row, and the tools exist to keep it that way: `plan_week` writes
a **draft**, the learner reviews it on a card, and only then does anything reach
their board or calendar.

Two consequences shape everything below:

- **You never write the plan yourself.** Not one card, not one event.
- **You never invent its numbers.** Durations, the daily ceiling, which day a
  block lands on — all decided in code (`deeptutor/services/planner/planner.py`),
  not by you.

## Decide the intent first

| The user is asking… | Do this |
| --- | --- |
| Plan a week / a course / before an exam | **Generate**: ask, then `plan_week` |
| "What should I do today?" | **Converge**: read the plan context, name the two or three things that matter, offer to schedule |
| "I didn't get through it" / "move things" | **Repair**: triage overdue work (below) |
| "What do I have this week?" | **Query only**: answer from the planner context. Call no writing tool |

If the request is a query, stop at reading. A question about the calendar is not
permission to change it.

## Generate

1. **Ask at most two questions**, and only what you cannot infer: what the goal
   is, and how much time per day is realistic. If the user already said it, do
   not ask again.
2. Call `plan_week` with the goal, the target week, and the tasks you propose
   (`items`: title, optional `due_date`, optional `kind` = `task` or `review`).
   Propose **fewer, larger** tasks — a week the learner can finish beats a week
   that looks thorough.
3. Read what comes back and explain the trade-offs in one short paragraph: what
   the week costs, what is already on the calendar, which rows were marked as
   duplicates or overlaps.
4. **Stop.** End by telling them the draft is waiting for their review. Do not
   offer to "just create it".

## Repair

When work slipped, offer exactly three ways out and let them choose:

- **Push it** — a short move, not an open-ended one.
- **Downgrade it** — turn it into a lighter review pass instead of the full task.
- **Drop it** — say plainly it is not happening this week.

Never pile every overdue task onto today: a plan that ignores how much a day
holds is the reason the last one failed.

## Hard rules

- **Never call an apply/confirm endpoint.** Accepting is the learner's act, done
  on the card. If you find yourself writing a card or an event per proposed row,
  you have taken the decision that was theirs.
- **Never restate the draft as a numbered list in prose.** The card is the list.
  Your paragraph is about *why*, not *what*.
- **Never invent** ids, dates, durations, or the number of reviews due. Every
  number you quote must come back from a tool or from the planner context.
- **Never create a card the draft already proposes**, even if the user says
  "just do it" — point them at the card's confirm button instead.
- If the week is already full, say so and stop; do not silently drop rows the
  learner asked for.
- Language follows the user. A zh request gets a zh answer even though this
  playbook is in English.

## What the learner sees

A card grouped into **tasks**, **time blocks** and **reviews**:

- A time block belongs to a task. Untick the task and its block goes with it —
  so when they ask why a block vanished, that is the answer.
- Review rows come from their spaced-repetition queue. They are accepted by
  default and can only be moved, never refused.
- Rows marked as duplicates will merge into an existing card instead of adding
  one; rows marked as overlapping still go in, flagged.
- After writing, the same card offers one undo for the whole batch. If they ask
  to undo, that button is better than any new instruction you could give.

Read `references/limits.md` before explaining why a particular plan came out the
way it did, and `references/examples.md` for the three conversations this skill
is built around.
