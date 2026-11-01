import type { StreamEvent } from '@/features/chat/model/protocol'
import { toolResultPayload } from '@/lib/tool-event'

/**
 * Reading a finished plan draft off a turn's stream.
 *
 * `plan_week` puts `plan_draft_id` on the tool result's metadata (see
 * `deeptutor/tools/planner_tools.py`); the id travels the same channel as
 * `mastery_handoff`, so there is no new stream event to learn about. The card
 * fetches the draft itself — the event carries an id, never a copy of the
 * rows, which keeps a replayed conversation small.
 *
 * Pure functions, so the selection rules can be tested without a DOM.
 */

/** Every draft id this turn produced, in the order they were produced. */
export function draftIdsFromEvents(events: StreamEvent[] | undefined): string[] {
  const ids: string[] = []
  for (const event of events ?? []) {
    if (event.type !== 'tool_result') continue
    const value = toolResultPayload(event.metadata, 'plan_draft_id')
    const id = typeof value === 'string' ? value.trim() : ''
    if (id && !ids.includes(id)) ids.push(id)
  }
  return ids
}

/**
 * The one draft worth showing as a card in this turn.
 *
 * A turn that planned twice (`plan_week`, then a revised `plan_week`) leaves
 * two ids behind. Only the last is offered for review: the earlier rows were
 * superseded by the same assistant, and showing both invites accepting a plan
 * the answer has already moved past.
 */
export function latestDraftId(events: StreamEvent[] | undefined): string | null {
  const ids = draftIdsFromEvents(events)
  return ids.length ? ids[ids.length - 1] : null
}

/** Ids this turn produced that are not the one on show, newest first. */
export function supersededDraftIds(events: StreamEvent[] | undefined): string[] {
  const ids = draftIdsFromEvents(events)
  return ids.slice(0, -1).reverse()
}
