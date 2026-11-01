import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import translations from '@/locales/en/app.json'
import type { PlanDraft, PlanItem } from '@/lib/planner-api'
import PlanDraftCard from '@/features/planner/components/PlanDraftCard'

const mocks = vi.hoisted(() => ({ applyDraft: vi.fn(), undoBatch: vi.fn(), patchItem: vi.fn() }))

vi.mock('@/lib/planner-api', () => ({
  applyDraft: mocks.applyDraft,
  undoBatch: mocks.undoBatch,
  patchItem: mocks.patchItem,
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

function item(overrides: Partial<PlanItem> & Pick<PlanItem, 'item_id' | 'kind' | 'title'>): PlanItem {
  return {
    draft_id: 'd1',
    due_date: '2026-11-03',
    start_at: null,
    duration_min: 45,
    depends_on: null,
    conflict: 'none',
    source: 'plan',
    locked: false,
    decision: 'pending',
    position: 0,
    ...overrides,
  }
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
      item({ item_id: 'i1', kind: 'task', title: 'Eigenvalues' }),
      item({
        item_id: 'i2',
        kind: 'block',
        title: 'Eigenvalues',
        start_at: '2026-11-03T08:00:00+08:00',
        depends_on: 'i1',
        duration_min: 45,
      }),
      item({
        item_id: 'i3',
        kind: 'review',
        title: '12 reviews due',
        source: 'srs',
        locked: true,
        duration_min: 60,
      }),
    ],
    ...overrides,
  }
}

function withItems(items: PlanItem[]): PlanDraft {
  return draft({ items })
}

beforeEach(() => {
  mocks.applyDraft.mockReset()
  mocks.patchItem.mockReset()
  mocks.patchItem.mockImplementation((_draftId: string, itemId: string, changes: Record<string, unknown>) =>
    Promise.resolve(
      withItems(
        draft().items.map(row => (row.item_id === itemId ? { ...row, ...changes } : row))
      )
    )
  )
})

describe('editing a row before accepting it', () => {
  it('changes a task date and sends only that field', async () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[0])
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-06' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(mocks.patchItem).toHaveBeenCalledTimes(1))
    expect(mocks.patchItem).toHaveBeenCalledWith('d1', 'i1', { due_date: '2026-11-06' })
    await waitFor(() => expect(screen.getByText(/2026-11-06/)).toBeTruthy())
  })

  it('changes a block duration and sends only that field', async () => {
    render(<PlanDraftCard draft={draft()} />)

    // The task and its block share a title; the block's row is the second.
    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[1])
    fireEvent.change(screen.getByLabelText('Duration (min)'), { target: { value: '30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(mocks.patchItem).toHaveBeenCalledTimes(1))
    expect(mocks.patchItem).toHaveBeenCalledWith('d1', 'i2', { duration_min: 30 })
  })

  it('keeps the offset of the time it was given when the start moves', async () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[1])
    fireEvent.change(screen.getByLabelText('Start time'), { target: { value: '19:30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(mocks.patchItem).toHaveBeenCalledTimes(1))
    expect(mocks.patchItem).toHaveBeenCalledWith('d1', 'i2', {
      start_at: '2026-11-03T19:30:00+08:00',
    })
  })

  it('leaves the selection alone when a date changes', async () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getByLabelText('12 reviews due'))
    expect((screen.getByLabelText('12 reviews due') as HTMLInputElement).checked).toBe(true)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[0])
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-06' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(mocks.patchItem).toHaveBeenCalled())
    expect(screen.getByText('3/3 selected · 2.5h')).toBeTruthy()
  })

  it('rolls the row back when the server refuses the change', async () => {
    mocks.patchItem.mockRejectedValueOnce(new Error('A draft item cannot be changed after apply.'))
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[0])
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-06' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() =>
      expect(screen.getByRole('alert').textContent).toContain('cannot be changed')
    )
    expect(screen.queryByText(/2026-11-06/)).toBeNull()
  })

  it('will not accept while a row is open for editing', () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[0])

    expect((screen.getByRole('button', { name: /Write/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('lets a locked review row move but keeps it impossible to skip', async () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Edit: 12 reviews due' }))
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-05' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(mocks.patchItem).toHaveBeenCalledWith('d1', 'i3', { due_date: '2026-11-05' }))
    expect((screen.getByLabelText('12 reviews due') as HTMLInputElement).disabled).toBe(true)
  })

  it('cancels without calling the server', () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getAllByRole('button', { name: 'Edit: Eigenvalues' })[0])
    fireEvent.change(screen.getByLabelText('Due date'), { target: { value: '2026-11-06' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(mocks.patchItem).not.toHaveBeenCalled()
    expect(screen.queryByText(/2026-11-06/)).toBeNull()
  })
})
