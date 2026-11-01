import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import translations from '@/locales/en/app.json'
import type { PlanDraft } from '@/lib/planner-api'
import type { StreamEvent } from '@/features/chat/model/protocol'
import {
  draftIdsFromEvents,
  latestDraftId,
  supersededDraftIds,
} from '@/lib/planner-state'
import PlanDraftPanel from '@/features/planner/components/PlanDraftPanel'

const mocks = vi.hoisted(() => ({ getDraft: vi.fn(), applyDraft: vi.fn() }))

vi.mock('@/lib/planner-api', () => ({
  getDraft: mocks.getDraft,
  applyDraft: mocks.applyDraft,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      String((translations as Record<string, string>)[key] ?? key).replace(
        /\{\{(\w+)\}\}/g,
        (_match, name: string) => String(options?.[name] ?? '')
      ),
    i18n: { language: 'en' },
  }),
}))

function toolResult(id: string, metadata: Record<string, unknown>): StreamEvent {
  return {
    type: 'tool_result',
    source: 'plan_week',
    stage: '',
    content: '',
    metadata: { tool_metadata: { plan_draft_id: id, ...metadata } },
    timestamp: 0,
  } as unknown as StreamEvent
}

function draft(status: PlanDraft['status'] = 'reviewing'): PlanDraft {
  return {
    id: 'd1',
    session_id: 's1',
    workspace_id: 'study',
    tzid: 'Asia/Shanghai',
    status,
    created_at: '2026-11-01T00:00:00Z',
    expires_at: '2026-11-02T00:00:00Z',
    calendar_revision: 0,
    board_revision: 0,
    summary: { goal: 'Midterm', weekly_limit_min: 300 },
    apply_result: null,
    undo_token: null,
    applied_at: null,
    items: [
      {
        draft_id: 'd1',
        item_id: 'i1',
        kind: 'task',
        title: 'Eigenvalues',
        due_date: '2026-11-03',
        start_at: null,
        duration_min: 45,
        depends_on: null,
        conflict: 'none',
        source: 'plan',
        locked: false,
        decision: 'pending',
        position: 0,
      },
    ],
  }
}

beforeEach(() => {
  mocks.getDraft.mockReset()
  mocks.applyDraft.mockReset()
  mocks.getDraft.mockResolvedValue(draft())
})

describe('reading a draft id off the stream', () => {
  it('finds the id a finished tool result carries', () => {
    expect(draftIdsFromEvents([toolResult('d1', {})])).toEqual(['d1'])
  })

  it('ignores events that are not tool results', () => {
    const other = { ...toolResult('d9', {}), type: 'content' } as unknown as StreamEvent
    expect(draftIdsFromEvents([other])).toEqual([])
  })

  it('ignores a tool result with no draft id', () => {
    const event = {
      type: 'tool_result',
      source: 'tasks',
      stage: '',
      content: '',
      metadata: { tool_metadata: { task_ids: ['t1'] } },
      timestamp: 0,
    } as unknown as StreamEvent
    expect(draftIdsFromEvents([event])).toEqual([])
  })

  it('keeps only the newest draft of a turn, and names the older ones', () => {
    const events = [toolResult('d1', {}), toolResult('d2', {})]

    expect(latestDraftId(events)).toBe('d2')
    expect(supersededDraftIds(events)).toEqual(['d1'])
  })

  it('has nothing to show for an empty turn', () => {
    expect(latestDraftId(undefined)).toBeNull()
    expect(latestDraftId([])).toBeNull()
    expect(supersededDraftIds([])).toEqual([])
  })
})

describe('PlanDraftPanel', () => {
  it('loads the draft it was named and renders the review card', async () => {
    render(<PlanDraftPanel draftId="d1" />)

    await waitFor(() => expect(screen.getByText('Eigenvalues')).toBeTruthy())
    expect(mocks.getDraft).toHaveBeenCalledWith('d1', expect.anything())
    expect(screen.getByRole('button', { name: /Write/ })).toBeTruthy()
  })

  it('shows an expired draft as one line instead of an error', async () => {
    mocks.getDraft.mockRejectedValueOnce(new Error('Plan draft not found.'))

    render(<PlanDraftPanel draftId="gone" />)

    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('expired'))
    expect(mocks.getDraft).toHaveBeenCalledTimes(1) // no retry loop
  })

  it('opens an already-applied draft read-only', async () => {
    mocks.getDraft.mockResolvedValueOnce(draft('applied'))

    render(<PlanDraftPanel draftId="d1" />)

    await waitFor(() => expect(screen.getByText(/Written/)).toBeTruthy())
    expect(screen.queryByRole('button', { name: /Write/ })).toBeNull()
  })

  it('re-reads when the panel is pointed at another draft', async () => {
    const { rerender } = render(<PlanDraftPanel draftId="d1" />)
    await waitFor(() => expect(mocks.getDraft).toHaveBeenCalledTimes(1))

    rerender(<PlanDraftPanel draftId="d2" />)

    await waitFor(() => expect(mocks.getDraft).toHaveBeenCalledTimes(2))
    expect(mocks.getDraft).toHaveBeenLastCalledWith('d2', expect.anything())
  })

  it('writes through the card it renders', async () => {
    mocks.applyDraft.mockResolvedValueOnce({
      applied: 1,
      merged: 0,
      skipped: 0,
      task_ids: ['c1'],
      event_ids: [],
      undo_token: 'tok',
      revision: {},
    })
    render(<PlanDraftPanel draftId="d1" />)

    const confirm = await screen.findByRole('button', { name: /Write/ })
    fireEvent.click(confirm)

    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft).toHaveBeenCalledWith('d1', { i1: 'accepted' })
  })
})
