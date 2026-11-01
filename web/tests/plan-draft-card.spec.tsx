import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import translations from '@/locales/en/app.json'
import type { PlanDraft, PlanItem } from '@/lib/planner-api'
import PlanDraftCard from '@/features/planner/components/PlanDraftCard'

const mocks = vi.hoisted(() => ({ applyDraft: vi.fn() }))

vi.mock('@/lib/planner-api', () => ({ applyDraft: mocks.applyDraft }))

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
    summary: { goal: 'Linear algebra midterm', weekly_limit_min: 300 },
    apply_result: null,
    undo_token: null,
    applied_at: null,
    items: [
      item({ item_id: 'i1', kind: 'task', title: 'Eigenvalues', due_date: '2026-11-03' }),
      item({ item_id: 'i2', kind: 'task', title: 'Redo mistakes', due_date: '2026-11-04' }),
      item({
        item_id: 'i3',
        kind: 'block',
        title: 'Eigenvalues',
        start_at: '2026-11-03T08:00:00+08:00',
        depends_on: 'i1',
        duration_min: 45,
      }),
      item({
        item_id: 'i4',
        kind: 'review',
        title: '12 reviews due',
        source: 'srs',
        locked: true,
        duration_min: 60,
        due_date: '2026-11-02',
      }),
    ],
    ...overrides,
  }
}

beforeEach(() => {
  mocks.applyDraft.mockReset()
  mocks.applyDraft.mockResolvedValue({
    applied: 4,
    merged: 0,
    skipped: 0,
    task_ids: ['c1', 'c2'],
    event_ids: ['e1'],
    undo_token: 'tok',
    revision: {},
  })
})

describe('PlanDraftCard', () => {
  it('renders the three groups', () => {
    render(<PlanDraftCard draft={draft()} />)

    expect(screen.getByText('2 new task(s)')).toBeTruthy()
    expect(screen.getByText('1 time block(s)')).toBeTruthy()
    expect(screen.getByText('1 review row(s)')).toBeTruthy()
    // The task and its block share a title on purpose; the card shows both.
    expect(screen.getAllByText('Eigenvalues')).toHaveLength(2)
    expect(screen.getByText('12 reviews due')).toBeTruthy()
  })

  it('dismisses a block with the task it serves, and will not re-select it alone', () => {
    render(<PlanDraftCard draft={draft()} />)

    const task = screen.getAllByLabelText('Eigenvalues')[0] as HTMLInputElement
    fireEvent.click(task)

    const block = screen.getAllByLabelText('Eigenvalues')[1] as HTMLInputElement
    expect(block).not.toBe(task)
    expect(block.checked).toBe(false)
    expect(block.disabled).toBe(true)

    fireEvent.click(block)
    expect(block.checked).toBe(false)
  })

  it('keeps locked rows checked and unusable', () => {
    render(<PlanDraftCard draft={draft()} />)

    const locked = screen.getByLabelText('12 reviews due') as HTMLInputElement
    expect(locked.checked).toBe(true)
    expect(locked.disabled).toBe(true)

    fireEvent.click(locked)
    expect(locked.checked).toBe(true)
  })

  it('counts what is selected and how long it takes', () => {
    render(<PlanDraftCard draft={draft()} />)

    expect(screen.getByText('4/4 selected · 3.3h')).toBeTruthy()

    fireEvent.click(screen.getByLabelText('Redo mistakes'))
    expect(screen.getByText('3/4 selected · 2.5h')).toBeTruthy()
  })

  it('lets the learner clear and restore everything', () => {
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getByRole('button', { name: 'Select none' }))
    expect(screen.getByText('1/4 selected · 1.0h')).toBeTruthy() // only the locked row
    expect((screen.getByRole('button', { name: /Write/ }) as HTMLButtonElement).disabled).toBe(
      false
    )

    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByText('4/4 selected · 3.3h')).toBeTruthy()
  })

  it('cannot write while nothing selectable is chosen', () => {
    const onlyLocked = draft({
      items: [item({ item_id: 'i9', kind: 'review', title: '1 review due', locked: true })],
      summary: { weekly_limit_min: 300 },
    })
    // One locked row means one selected row; to reach zero we need no rows at all.
    const empty = draft({ items: [], summary: { weekly_limit_min: 300 } })
    render(<PlanDraftCard draft={onlyLocked} />)
    expect((screen.getByRole('button', { name: /Write/ }) as HTMLButtonElement).disabled).toBe(
      false
    )

    document.body.innerHTML = ''
    render(<PlanDraftCard draft={empty} />)
    expect((screen.getByRole('button', { name: /Write/ }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('writes exactly the decisions on screen, once', async () => {
    render(<PlanDraftCard draft={draft()} />)
    fireEvent.click(screen.getByLabelText('Redo mistakes'))

    const confirm = screen.getByRole('button', { name: 'Write 3 item(s)' }) as HTMLButtonElement
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    await waitFor(() => expect(mocks.applyDraft).toHaveBeenCalledTimes(1))
    expect(mocks.applyDraft).toHaveBeenCalledWith('d1', {
      i1: 'accepted',
      i2: 'dismissed',
      i3: 'accepted',
      i4: 'accepted',
    })
  })

  it('keeps the selection and shows the reason when writing fails', async () => {
    mocks.applyDraft.mockRejectedValueOnce(new Error('Draft is expired'))
    render(<PlanDraftCard draft={draft()} />)

    fireEvent.click(screen.getByLabelText('Redo mistakes'))
    fireEvent.click(screen.getByRole('button', { name: /Write/ }))

    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('expired'))
    expect(screen.getByText('3/4 selected · 2.5h')).toBeTruthy()
  })

  it('warns about the weekly limit without blocking the write', () => {
    render(<PlanDraftCard draft={draft({ summary: { weekly_limit_min: 60 } })} />)

    expect(screen.getByRole('status').textContent).toContain('1.0h')
    expect((screen.getByRole('button', { name: /Write/ }) as HTMLButtonElement).disabled).toBe(
      false
    )
  })

  it('shows a written draft without checkboxes', () => {
    render(
      <PlanDraftCard
        draft={draft({
          status: 'applied',
          apply_result: {
            applied: 4,
            merged: 0,
            skipped: 0,
            task_ids: [],
            event_ids: [],
            undo_token: 'tok',
            revision: {},
          },
        })}
      />
    )

    expect(screen.queryByRole('button', { name: /Write/ })).toBeNull()
    expect(screen.getByText(/Written/)).toBeTruthy()
    expect((screen.getAllByLabelText('Eigenvalues')[0] as HTMLInputElement).disabled).toBe(true)
  })

  it('marks duplicates and overlaps so the learner is not surprised', () => {
    render(
      <PlanDraftCard
        draft={draft({
          items: [
            item({ item_id: 'i1', kind: 'task', title: 'Already there', conflict: 'duplicate' }),
            item({
              item_id: 'i2',
              kind: 'block',
              title: 'Clashing block',
              conflict: 'overlap',
              depends_on: 'i1',
              start_at: '2026-11-04T19:00:00+08:00',
            }),
          ],
        })}
      />
    )

    expect(screen.getByText('Merges with an existing task')).toBeTruthy()
    expect(screen.getByText('Overlaps an existing event')).toBeTruthy()
  })
})
