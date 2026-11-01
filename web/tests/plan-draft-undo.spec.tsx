import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import translations from '@/locales/en/app.json'
import type { ApplyOutcome, PlanDraft } from '@/lib/planner-api'
import PlanDraftCard from '@/features/planner/components/PlanDraftCard'

const mocks = vi.hoisted(() => ({ applyDraft: vi.fn(), undoBatch: vi.fn() }))

vi.mock('@/lib/planner-api', () => ({
  applyDraft: mocks.applyDraft,
  undoBatch: mocks.undoBatch,
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

const OUTCOME: ApplyOutcome = {
  applied: 2,
  merged: 0,
  skipped: 0,
  task_ids: ['c1'],
  event_ids: ['e1'],
  undo_token: 'tok-1',
  revision: {},
}

function draft(overrides: Partial<PlanDraft> = {}): PlanDraft {
  return {
    id: 'd1',
    session_id: 's1',
    workspace_id: 'study',
    tzid: 'Asia/Shanghai',
    status: 'reviewing',
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
      {
        draft_id: 'd1',
        item_id: 'i2',
        kind: 'task',
        title: 'Redo mistakes',
        due_date: '2026-11-04',
        start_at: null,
        duration_min: 45,
        depends_on: null,
        conflict: 'none',
        source: 'plan',
        locked: false,
        decision: 'pending',
        position: 1,
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  mocks.applyDraft.mockReset()
  mocks.undoBatch.mockReset()
  mocks.applyDraft.mockResolvedValue(OUTCOME)
  mocks.undoBatch.mockResolvedValue({
    removed: 2,
    skipped: [],
    already_undone: false,
    revision: {},
  })
})

async function written() {
  render(<PlanDraftCard draft={draft()} />)
  fireEvent.click(screen.getByRole('button', { name: /Write/ }))
  await screen.findByText('2 item(s) written')
}

describe('undoing a written batch', () => {
  it('offers the undo the moment the batch is written', async () => {
    await written()

    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Write/ })).toBeNull()
  })

  it('asks once, inline, before taking anything back', async () => {
    await written()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(mocks.undoBatch).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Take it back?' }))
    await waitFor(() => expect(mocks.undoBatch).toHaveBeenCalledTimes(1))
    expect(mocks.undoBatch).toHaveBeenCalledWith('tok-1')
    await screen.findByText('Took back 2')
  })

  it('backs out of the confirmation without calling the server', async () => {
    await written()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(screen.queryByRole('button', { name: 'Take it back?' })).toBeNull()
    expect(mocks.undoBatch).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy()
  })

  it('names the rows it kept because they were edited since', async () => {
    mocks.undoBatch.mockResolvedValueOnce({
      removed: 1,
      skipped: ['Redo mistakes'],
      already_undone: false,
      revision: {},
    })
    await written()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Take it back?' }))

    await screen.findByText('Edited since, so kept: Redo mistakes')
    expect(screen.getByText('Took back 1')).toBeTruthy()
  })

  it('treats an already-undone batch as undone, not as an error', async () => {
    mocks.undoBatch.mockResolvedValueOnce({
      removed: 0,
      skipped: [],
      already_undone: true,
      revision: {},
    })
    await written()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Take it back?' }))

    await screen.findByText('Took back 0')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('keeps the undo button when the call fails, and says why', async () => {
    mocks.undoBatch.mockRejectedValueOnce(new Error('Plan draft not found.'))
    await written()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Take it back?' }))

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('Plan draft not found.')
    )
    // The confirmation stays open: the learner meant to undo, so the retry is
    // one click, not a second journey through the same question.
    expect(screen.getByRole('button', { name: 'Take it back?' })).toBeTruthy()
  })

  it('reopens a draft that was already applied, with its undo intact', () => {
    render(<PlanDraftCard draft={draft({ status: 'applied', apply_result: OUTCOME })} />)

    expect(screen.getByText('2 item(s) written')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy()
    expect((screen.getByLabelText('Eigenvalues') as HTMLInputElement).disabled).toBe(true)
  })
})
