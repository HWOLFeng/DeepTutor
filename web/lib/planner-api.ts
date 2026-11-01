import { apiFetch } from '@/shared/api/client'

/**
 * The planner's three verbs: read a draft, adjust a row, accept a batch.
 *
 * Applying is deliberately a plain HTTP call from the card rather than a tool
 * the assistant can invoke — the person reading the card decides what is
 * adopted, and the model only ever sees the fact that it happened.
 */
export type DraftStatus = 'drafting' | 'reviewing' | 'applied' | 'undone' | 'expired'
export type Decision = 'pending' | 'accepted' | 'dismissed'
export type ItemKind = 'task' | 'block' | 'review'
export type Conflict = 'none' | 'duplicate' | 'overlap'

export interface PlanItem {
  draft_id: string
  item_id: string
  kind: ItemKind
  title: string
  /** A plain calendar day ("YYYY-MM-DD"), never shifted through UTC. */
  due_date: string | null
  start_at: string | null
  duration_min: number | null
  /** The task row this time block serves, as a draft item id. */
  depends_on: string | null
  conflict: Conflict
  source: 'plan' | 'srs'
  /** Locked rows (spaced repetition) are accepted by default and cannot be dismissed. */
  locked: boolean
  decision: Decision
  position: number
}

export interface PlanDraftSummary {
  goal?: string
  week_start?: string
  daily_max_min?: number
  weekly_limit_min?: number
  free_minutes?: number
  dropped_blocks?: number
  duplicates?: number
  overlaps?: number
  review_due?: number
}

export interface ApplyOutcome {
  applied: number
  merged: number
  skipped: number
  task_ids: string[]
  event_ids: string[]
  undo_token: string
  revision: Record<string, number>
}

export interface PlanDraft {
  id: string
  session_id: string
  workspace_id: string
  tzid: string
  status: DraftStatus
  created_at: string
  expires_at: string
  calendar_revision: number
  board_revision: number
  summary: PlanDraftSummary
  apply_result: ApplyOutcome | null
  undo_token: string | null
  applied_at: string | null
  items: PlanItem[]
}

export type PlanItemPatch = Partial<Pick<PlanItem, 'title' | 'due_date' | 'start_at' | 'duration_min'>>

const endpoint = '/api/planner'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(`${endpoint}${path}`, init)
  if (!response.ok) {
    const detail = await response.json().catch(() => null)
    throw new Error(
      (detail && typeof detail.detail === 'string' && detail.detail) ||
        `Plan request failed (${response.status})`
    )
  }
  return (await response.json()) as T
}

export function getDraft(draftId: string, signal?: AbortSignal): Promise<PlanDraft> {
  return request<PlanDraft>(`/drafts/${encodeURIComponent(draftId)}`, {
    signal,
    cache: 'no-store',
  })
}

export function patchItem(
  draftId: string,
  itemId: string,
  changes: PlanItemPatch
): Promise<PlanDraft> {
  return request<PlanDraft>(
    `/drafts/${encodeURIComponent(draftId)}/items/${encodeURIComponent(itemId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(changes),
    }
  )
}

export function applyDraft(
  draftId: string,
  decisions: Record<string, Decision>
): Promise<ApplyOutcome> {
  return request<ApplyOutcome>(`/drafts/${encodeURIComponent(draftId)}/apply`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
}

/** Total minutes a set of accepted rows would put on the calendar. */
export function selectedMinutes(draft: PlanDraft, isSelected: (item: PlanItem) => boolean): number {
  return draft.items.reduce(
    (total, item) => (isSelected(item) ? total + (item.duration_min ?? 0) : total),
    0
  )
}
