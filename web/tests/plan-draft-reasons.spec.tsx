import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    reason: null,
    position: 0,
    ...overrides,
  } as PlanItem
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
      item({ item_id: 'i2', kind: 'task', title: 'Redo mistakes' }),
    ],
    ...overrides,
  }
}

beforeEach(() => {
  mocks.applyDraft.mockReset()
  mocks.applyDraft.mockResolvedValue({
    applied: 1,
    merged: 0,
    skipped: 1,
    task_ids: ['c1'],
    event_ids: [],
    undo_token: 'tok',
    revision: {},
  })
})

function dismiss(title: string) {
  fireEvent.click(screen.getByLabelText(title))
}

/** The tags live inside the row they describe, so query them in that row. */
function tag(title: string, name: string) {
  const row = screen.getByLabelText(title).closest('li') as HTMLElement
  fireEvent.click(within(row).getByRole('button', { name }))
}

function tagIn(title: string, name: string) {
  const row = screen.getByLabelText(title).closest('li') as HTMLElement
  return within(row).getByRole('button', { name })
}

describe('refusing a row with a reason', () => {
  it('offers the three tags only once a row is refused', () => {
    render(<PlanDraftCard draft={draft()} />)

    expect(screen.queryByRole('button', { name: 'Wrong time' })).toBeNull()

    dismiss('Eigenvalues')

    expect(screen.getByRole('button', { name: 'Wrong time' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Not what I need' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Already have this' })).toBeTruthy()
  })

  it('sends the chosen tag with the decisions', async () => {
    render(<PlanDraftCard draft={draft()} />)

    dismiss('Eigenvalues')
    tag('Eigenvalues', 'Wrong time')
    fireEvent.click(screen.getByRole('button', { name: /Write/ }))

    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft).toHaveBeenCalledWith(
      'd1',
      { i1: 'dismissed', i2: 'accepted' },
      { i1: 'time' }
    )
  })

  it('never makes the reason mandatory', async () => {
    render(<PlanDraftCard draft={draft()} />)

    dismiss('Eigenvalues')
    fireEvent.click(screen.getByRole('button', { name: /Write/ }))

    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft.mock.calls[0][2]).toEqual({})
  })

  it('unpicks a tag when it is clicked twice', async () => {
    render(<PlanDraftCard draft={draft()} />)

    dismiss('Eigenvalues')
    const chip = tagIn('Eigenvalues', 'Wrong time')
    fireEvent.click(chip)
    expect(chip.getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(chip)
    expect(chip.getAttribute('aria-pressed')).toBe('false')
  })

  it('drops the tag when the row is accepted again', async () => {
    render(<PlanDraftCard draft={draft()} />)

    dismiss('Eigenvalues')
    tag('Eigenvalues', 'Wrong time')
    fireEvent.click(screen.getByLabelText('Eigenvalues')) // re-accept

    expect(screen.queryByRole('button', { name: 'Wrong time' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Write/ }))
    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft).toHaveBeenCalledWith('d1', { i1: 'accepted', i2: 'accepted' }, {})
  })

  it('only carries tags for rows that are still refused', async () => {
    render(<PlanDraftCard draft={draft()} />)

    dismiss('Eigenvalues')
    tag('Eigenvalues', 'Wrong time')
    dismiss('Redo mistakes')
    tag('Redo mistakes', 'Already have this')
    // Re-accept the first row, keeping its tag would be a lie.
    fireEvent.click(screen.getByLabelText('Eigenvalues'))
    fireEvent.click(screen.getByRole('button', { name: /Write/ }))

    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft).toHaveBeenCalledWith(
      'd1',
      { i1: 'accepted', i2: 'dismissed' },
      { i2: 'duplicate' }
    )
  })
})
