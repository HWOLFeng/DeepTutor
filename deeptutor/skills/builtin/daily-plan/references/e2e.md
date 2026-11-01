# End-to-end walkthrough: does the morning plan actually work?

Everything here crosses process and page boundaries — a scheduler, a turn, a
card, two stores — so no unit test can prove it. Walk it once, write down what
you saw on each line, and keep the row numbers when reporting.

Prerequisites: the `calendar` module enabled (the `cron` tool belongs to it), a
conversation in a workspace, and a built frontend.

| # | Do this | Expected | Where it can break silently |
| --- | --- | --- | --- |
| 1 | In a conversation, say: "每天早上 7:30 帮我排今天的计划" | It calls `cron` with `action="list"` first, then schedules one job, and names how to cancel it | The model schedules without listing, and a second job appears later |
| 2 | Say the same thing again | No second job: it reports the existing one, or offers to change its time | A second job; two reminders arrive a minute apart |
| 3 | Open the calendar and look at the automation layer | One entry at 07:30, with a readable title/message | Nothing appears — usually because the job was created in another workspace, and the layer is workspace-filtered |
| 4 | Trigger it now: schedule a second job a couple of minutes out, or wait for the real one | A new message arrives **in this conversation** | It lands in a different session, or nowhere |
| 5 | Look at that message | A plan card, and **nothing written**: the task board and calendar are unchanged | Cards already exist — the job's message asked for a plan instead of a draft |
| 6 | Press "Write the selected rows" | Tasks and time blocks appear; the card offers undo | Rows appear but the card shows no undo |
| 7 | Cancel the automation from the calendar (or with the `cron` tool's `cancel`) | The entry disappears and nothing fires the next morning | The entry disappears but the job keeps firing |

## Two failures worth knowing by name

**The self-contained message.** Step 4's turn cannot see the conversation that
created the job. If the job's `message` says "plan the rest like we discussed",
the morning turn has no idea what was discussed and will produce something
generic — or ask a question nobody is present to answer.

**The draft, not the write.** If the job's `message` says "put my plan in the
calendar", the morning turn will do exactly that, and the learner wakes up to a
calendar they did not approve. The message must say *draft*, and the turn must
stop at the card.

## What this walkthrough does not cover

- Delivery of the reminder to a phone or desktop notification — that is the
  scheduler's own surface, not this feature's.
- Multi-user installs, where the calendar layer is scoped per account and per
  workspace; step 3's failure mode is the one to check first there.
- Anything about the plan's *quality*: that is what the review card is for.
