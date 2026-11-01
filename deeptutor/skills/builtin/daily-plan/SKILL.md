---
name: daily-plan
description: Set up a recurring morning study-plan routine that produces a plan draft for the day, using the cron tool. Use when the user asks to be reminded each day, wants a standing "what should I do today" habit, or asks to automate their planning.
tags:
  - planner
---

# A daily plan, on a schedule

"What should I do today?" is the question people ask most and think to ask
least. This playbook puts it on a timer: one `cron` job that, each morning,
starts a fresh turn in this conversation which builds a plan draft for the day.

**No new machinery is involved.** The reminder is an ordinary `cron` job; the
turn it starts is an ordinary turn; the plan it produces is the same draft the
learner reviews on a card. Your only job is to set it up honestly and to tell
them what it will do.

## Setting it up

1. **Look before writing.** Call `cron` with `action="list"` first. If a daily
   plan already exists, say so and offer to change its time — do not add a
   second one. Two reminders a morning is worse than none.
2. **Schedule it with a cron expression**, not a one-shot: `cron_expr` in the
   five-field form, and `tz` set to the reader's own zone. A reminder that fires
   at the right instant in the wrong zone is the classic failure here.
3. **Write a self-contained `message`.** The job runs in a *new turn*, which
   cannot see this conversation, the draft ids, or anything you said. The
   message must therefore carry the whole instruction — read today's calendar
   and what is due for review, propose the day's plan, and show it as a draft to
   confirm. Say explicitly that nothing may be written until the learner
   accepts it.
4. **One job per request.** Never create several "in case one fails".
5. **Name it for the learner**, not for the scheduler: "Morning plan", not
   "job_7f3a".

## Tell them the consequences

Before finishing, say three things plainly:

- it will appear as a **scheduled entry in their calendar**, where they can see
  it and cancel it;
- it **does not write to the board or the calendar** — each morning it only
  proposes, and the card is still their decision;
- how to stop it: the job id from `action="list"`, and `action="cancel"`.

If `cron` returns a failure, report the reason. Do not describe a reminder as
set up when the tool said otherwise — the learner will discover the silence at
seven in the morning, which is the worst possible moment.

## What the morning turn should look like

When the job fires, the instruction arrives as the turn's message. Treat it as
the learner asking "what should I do today": read the planner context, propose a
small day, and call `plan_week` for **that day only** — not the whole week. Then
stop at the card.

A day that holds three things is a good day. A day that holds the week is how
the last plan failed.

## When not to use this

- A single reminder ("remind me at 8") is the `cron` tool's job, not this
  playbook: no plan, no draft.
- A weekly review is a different rhythm; offer it separately rather than
  cramming both into one job.
- If the learner has never planned with you before, set the routine up *after*
  one good planning conversation — a daily reminder that produces a plan they
  have not seen the shape of will be cancelled within a week.

Read `references/e2e.md` for the walkthrough that proves the whole chain works
end to end, including the two places it can silently break.
