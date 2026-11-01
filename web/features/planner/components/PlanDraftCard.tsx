'use client'

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Lock } from 'lucide-react'
import {
  applyDraft,
  patchItem,
  undoBatch,
  type ApplyOutcome,
  type Decision,
  type ItemKind,
  type PlanDraft,
  type PlanItem,
} from '@/lib/planner-api'
import Tooltip from '@/shared/ui/Tooltip'
import './plan-draft.css'

/**
 * The review card: what the learner actually sees before anything is written.
 *
 * Three properties are load-bearing, and each is a test:
 *
 * - A time block follows its task. Dismissing the task dismisses the block and
 *   disables it, because a block with no task is time for work that will not
 *   exist.
 * - Locked rows (spaced repetition) are accepted and cannot be dismissed; only
 *   their date may move, and that is F12's job.
 * - "Select all" and "Select none" sit side by side. A learner who wants to
 *   start over should not have to untick nine boxes to say so.
 */
export default function PlanDraftCard({
  draft,
  onApplied,
  onCancel,
}: {
  draft: PlanDraft
  onApplied?: (outcome: ApplyOutcome) => void
  onCancel?: () => void
}) {
  const { t } = useTranslation()
  const [dismissed, setDismissed] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {}
    for (const item of draft.items) {
      if (item.decision === 'dismissed') initial[item.item_id] = true
    }
    return initial
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A local copy so an inline edit can land immediately and be rolled back if
  // the server refuses it. The server's rows win whenever they arrive.
  const [items, setItems] = useState<PlanItem[]>(draft.items)
  const [editing, setEditing] = useState<string | null>(null)
  // Optional tags for refused rows, keyed by item id. Absent is a valid answer.
  const [reasons, setReasons] = useState<Record<string, string>>({})
  // The batch this card wrote. Seeded from the draft so a reload shows the same
  // state as the moment it was applied.
  const [written, setWritten] = useState<ApplyOutcome | null>(draft.apply_result ?? null)
  const [undoing, setUndoing] = useState(false)
  const [confirmingUndo, setConfirmingUndo] = useState(false)
  const [undone, setUndone] = useState<{ removed: number; skipped: string[] } | null>(null)

  const closed = draft.status !== 'drafting' && draft.status !== 'reviewing'
  const readOnly = closed || written !== null
  const byId = useMemo(() => new Map(items.map(item => [item.item_id, item])), [items])

  /** A block is selected only while the task it serves is selected. */
  function parentDismissed(item: PlanItem): boolean {
    if (item.kind !== 'block' || !item.depends_on) return false
    const parent = byId.get(item.depends_on)
    return parent ? Boolean(dismissed[parent.item_id]) : false
  }

  function isSelected(item: PlanItem): boolean {
    if (item.locked) return true
    if (item.kind === 'block') return !parentDismissed(item) && !dismissed[item.item_id]
    return !dismissed[item.item_id]
  }

  function isDisabled(item: PlanItem): boolean {
    if (readOnly || busy) return true
    if (item.locked) return true
    return item.kind === 'block' && parentDismissed(item)
  }

  const toggle = (item: PlanItem) => {
    if (isDisabled(item)) return
    setDismissed(previous => ({ ...previous, [item.item_id]: !previous[item.item_id] }))
    // Re-accepting a row drops whatever tag it had: a reason only explains a
    // refusal.
    setReasons(previous => {
      if (!previous[item.item_id]) return previous
      const next = { ...previous }
      delete next[item.item_id]
      return next
    })
  }

  const tagReason = (itemId: string, reason: string) => {
    setReasons(previous =>
      previous[itemId] === reason
        ? Object.fromEntries(Object.entries(previous).filter(([key]) => key !== itemId))
        : { ...previous, [itemId]: reason }
    )
  }

  const selectable = items.filter(item => !item.locked && !readOnly)
  const selected = items.filter(isSelected)
  const total = items.length
  const minutes = selected.reduce((sum, item) => sum + (item.duration_min ?? 0), 0)
  const hours = (minutes / 60).toFixed(1)
  const limit = draft.summary.weekly_limit_min
  const overBudget = typeof limit === 'number' && minutes > limit

  const confirmButton = (
    <button
      type="button"
      className="planner-draft__confirm"
      onClick={() => void confirm()}
      disabled={busy || selected.length === 0 || editing !== null}
    >
      {t('planner.draft.confirm', { n: selected.length })}
    </button>
  )

  async function confirm() {
    if (busy || !selected.length) return
    setBusy(true)
    setError(null)
    try {
      const decisions: Record<string, Decision> = {}
      for (const item of items) {
        decisions[item.item_id] = isSelected(item) ? 'accepted' : 'dismissed'
      }
      const refusedReasons: Record<string, string> = {}
      for (const item of items) {
        if (!isSelected(item) && reasons[item.item_id]) {
          refusedReasons[item.item_id] = reasons[item.item_id]
        }
      }
      const outcome = await applyDraft(draft.id, decisions, refusedReasons)
      setWritten(outcome)
      onApplied?.(outcome)
    } catch (failure) {
      // Keep the selection: retrying must not cost the learner their choices.
      setError(failure instanceof Error ? failure.message : t('planner.draft.error'))
    } finally {
      setBusy(false)
    }
  }

  function startEdit(item: PlanItem) {
    if (readOnly || busy) return
    setEditing(item.item_id)
  }

  /** Applies an edit immediately, then makes the server's copy the truth. */
  async function saveEdit(item: PlanItem, changes: Record<string, unknown>) {
    if (!Object.keys(changes).length) {
      setEditing(null)
      return
    }
    const previous = items
    const optimistic = items.map(row =>
      row.item_id === item.item_id ? { ...row, ...changes } : row
    )
    setItems(optimistic)
    setEditing(null)
    setError(null)
    try {
      const fresh = await patchItem(draft.id, item.item_id, changes)
      setItems(fresh.items)
    } catch (failure) {
      setItems(previous)
      setError(failure instanceof Error ? failure.message : t('planner.draft.editFailed'))
    }
  }

  async function undo() {
    if (!written || undoing) return
    setUndoing(true)
    setError(null)
    try {
      const outcome = await undoBatch(written.undo_token)
      if (outcome.already_undone) {
        setUndone({ removed: 0, skipped: outcome.skipped ?? [] })
      } else {
        setUndone({ removed: outcome.removed, skipped: outcome.skipped ?? [] })
      }
      setConfirmingUndo(false)
    } catch (failure) {
      // Keep the button: a failed undo is retryable, and the reason matters.
      setError(failure instanceof Error ? failure.message : t('planner.draft.undoFailed'))
    } finally {
      setUndoing(false)
    }
  }

  const groups: Array<{ kind: ItemKind; label: string }> = [
    { kind: 'task', label: t('planner.draft.group.tasks', { n: countOf(items, 'task') }) },
    { kind: 'block', label: t('planner.draft.group.blocks', { n: countOf(items, 'block') }) },
    { kind: 'review', label: t('planner.draft.group.reviews', { n: countOf(items, 'review') }) },
  ]

  return (
    <section className="planner-draft" aria-label={t('planner.draft.title')}>
      <header className="planner-draft__head">
        <div>
          <h3>{t('planner.draft.title')}</h3>
          <p className="planner-draft__sub">
            {draft.summary.goal ? `${draft.summary.goal} · ` : ''}
            {undone
              ? t('planner.draft.status.undone')
              : written
                ? t('planner.draft.status.applied')
                : readOnly
                  ? t(`planner.draft.status.${draft.status}`)
                  : t('planner.draft.unsaved')}
          </p>
        </div>
      </header>

      {groups.map(group =>
        countOf(items, group.kind) === 0 ? null : (
          <div key={group.kind} className="planner-draft__group">
            <h4>{group.label}</h4>
            <ul>
              {items
                .filter(item => item.kind === group.kind)
                .map(item => (
                  <li key={item.item_id} className="planner-draft__row">
                    <div className="planner-draft__line">
                    <label className={isDisabled(item) ? 'is-disabled' : undefined}>
                      <input
                        type="checkbox"
                        checked={isSelected(item)}
                        disabled={isDisabled(item)}
                        onChange={() => toggle(item)}
                        aria-label={item.title}
                      />
                      <span className="planner-draft__title">{item.title}</span>
                    </label>
                    <span className="planner-draft__meta">
                      {editing === item.item_id ? (
                        <EditFields
                          item={item}
                          disabled={busy}
                          onCancel={() => setEditing(null)}
                          onSave={changes => void saveEdit(item, changes)}
                        />
                      ) : (
                        <>
                          {when(item)}
                          {item.duration_min ? ` · ${item.duration_min}min` : ''}
                        </>
                      )}
                      {item.locked ? (
                        <span className="planner-draft__lock" title={t('planner.draft.locked')}>
                          <Lock size={12} aria-hidden />
                        </span>
                      ) : null}
                      {item.conflict === 'duplicate' ? (
                        <em className="planner-draft__flag">{t('planner.draft.duplicate')}</em>
                      ) : null}
                      {item.conflict === 'overlap' ? (
                        <em className="planner-draft__flag">{t('planner.draft.overlap')}</em>
                      ) : null}
                      {!readOnly && editing !== item.item_id ? (
                        <button
                          type="button"
                          className="planner-draft__edit"
                          onClick={() => startEdit(item)}
                          disabled={busy}
                          aria-label={`${t('planner.draft.edit')}: ${item.title}`}
                        >
                          {t('planner.draft.edit')}
                        </button>
                      ) : null}
                    </span>
                    </div>
                    {!readOnly && !isSelected(item) ? (
                      <span className="planner-draft__reasons">
                        {(['time', 'topic', 'duplicate'] as const).map(reason => (
                          <button
                            key={reason}
                            type="button"
                            className={
                              reasons[item.item_id] === reason ? 'is-picked' : undefined
                            }
                            onClick={() => tagReason(item.item_id, reason)}
                            disabled={busy}
                            aria-pressed={reasons[item.item_id] === reason}
                          >
                            {t(`planner.draft.reason.${reason}`)}
                          </button>
                        ))}
                      </span>
                    ) : null}
                  </li>
                ))}
            </ul>
            {group.kind === 'block' ? (
              <p className="planner-draft__hint">{t('planner.draft.blocksHint')}</p>
            ) : null}
          </div>
        )
      )}

      {overBudget ? (
        <p role="status" className="planner-draft__warn">
          {t('planner.draft.overBudget', { limit: ((limit ?? 0) / 60).toFixed(1) })}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="planner-draft__error">
          {error}
        </p>
      ) : null}

      {readOnly ? (
        <footer className="planner-draft__foot">
          <span className="planner-draft__count">
            {t('planner.draft.selected', { n: selected.length, total, hours })}
          </span>
          {written ? (
            <div className="planner-draft__actions">
              {undone ? (
                <span className="planner-draft__count">
                  {t('planner.draft.undone', { n: undone.removed })}
                </span>
              ) : confirmingUndo ? (
                <>
                  <button
                    type="button"
                    onClick={() => setConfirmingUndo(false)}
                    disabled={undoing}
                  >
                    {t('planner.draft.cancel')}
                  </button>
                  <button
                    type="button"
                    className="planner-draft__confirm"
                    onClick={() => void undo()}
                    disabled={undoing}
                  >
                    {t('planner.draft.undoConfirm')}
                  </button>
                </>
              ) : (
                <>
                  <span className="planner-draft__count">
                    {t('planner.draft.written', { n: written.applied })}
                  </span>
                  <button type="button" onClick={() => setConfirmingUndo(true)} disabled={undoing}>
                    {t('planner.draft.undo')}
                  </button>
                </>
              )}
            </div>
          ) : null}
        </footer>
      ) : (
        <footer className="planner-draft__foot">
          <span className="planner-draft__count">
            {t('planner.draft.selected', { n: selected.length, total, hours })}
          </span>
          <div className="planner-draft__actions">
            <button type="button" onClick={() => setDismissed({})} disabled={busy || !selectable.length}>
              {t('planner.draft.selectAll')}
            </button>
            <button
              type="button"
              onClick={() =>
                setDismissed(Object.fromEntries(selectable.map(item => [item.item_id, true])))
              }
              disabled={busy || !selectable.length}
            >
              {t('planner.draft.selectNone')}
            </button>
            {onCancel ? (
              <button type="button" onClick={onCancel} disabled={busy}>
                {t('planner.draft.cancel')}
              </button>
            ) : null}
            {selected.length === 0 ? (
              // A disabled button still has to say why it is disabled; the shared
              // tooltip keeps that hint reachable by keyboard and touch, which a
              // native title on a disabled control does not.
              <Tooltip label={t('planner.draft.atLeastOne')}>{confirmButton}</Tooltip>
            ) : (
              confirmButton
            )}
          </div>
        </footer>
      )}
      {undone && undone.skipped.length > 0 ? (
        <p className="planner-draft__hint" role="status">
          {t('planner.draft.undoSkipped', { titles: undone.skipped.join(', ') })}
        </p>
      ) : null}
    </section>
  )
}

/** Keeps the server's own offset, so an edited time is not shifted by the client. */
function withWallClock(startAt: string, clock: string): string {
  const [hour, minute] = clock.split(':').map(Number)
  const pad = (value: number) => String(value).padStart(2, '0')
  const datePart = startAt.slice(0, 10)
  const offset = startAt.slice(10).replace(/^T\d{2}:\d{2}(:\d{2}(\.\d+)?)?/, '')
  return `${datePart}T${pad(hour)}:${pad(minute)}:00${offset}`
}

function EditFields({
  item,
  disabled,
  onSave,
  onCancel,
}: {
  item: PlanItem
  disabled: boolean
  onSave: (changes: Record<string, unknown>) => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  const [date, setDate] = useState(item.due_date ?? '')
  const [clock, setClock] = useState(item.start_at ? item.start_at.slice(11, 16) : '08:00')
  const [minutes, setMinutes] = useState(String(item.duration_min ?? 45))

  const save = () => {
    if (item.kind === 'block') {
      const duration = Number(minutes)
      const changes: Record<string, unknown> = {}
      if (Number.isFinite(duration) && duration > 0 && duration !== item.duration_min) {
        changes.duration_min = duration
      }
      if (item.start_at && withWallClock(item.start_at, clock) !== item.start_at) {
        changes.start_at = withWallClock(item.start_at, clock)
      }
      onSave(changes)
      return
    }
    const changes: Record<string, unknown> = {}
    if (date && date !== item.due_date) changes.due_date = date
    onSave(changes)
  }

  return (
    <span className="planner-draft__editor">
      {item.kind === 'block' ? (
        <>
          <input
            type="time"
            value={clock}
            onChange={event => setClock(event.target.value)}
            disabled={disabled}
            aria-label={t('planner.draft.startTime')}
          />
          <input
            type="number"
            min={1}
            max={600}
            value={minutes}
            onChange={event => setMinutes(event.target.value)}
            disabled={disabled}
            aria-label={t('planner.draft.duration')}
          />
        </>
      ) : (
        <input
          type="date"
          value={date}
          onChange={event => setDate(event.target.value)}
          disabled={disabled}
          aria-label={t('planner.draft.dueDate')}
        />
      )}
      <button type="button" onClick={save} disabled={disabled}>
        {t('planner.draft.save')}
      </button>
      <button type="button" onClick={onCancel} disabled={disabled}>
        {t('planner.draft.cancel')}
      </button>
    </span>
  )
}

function countOf(items: PlanItem[], kind: ItemKind): number {
  return items.filter(item => item.kind === kind).length
}

function when(item: PlanItem): string {
  if (item.start_at) {
    const start = new Date(item.start_at)
    if (!Number.isNaN(start.getTime())) {
      const pad = (value: number) => String(value).padStart(2, '0')
      return `${pad(start.getMonth() + 1)}/${pad(start.getDate())} ${pad(start.getHours())}:${pad(start.getMinutes())}`
    }
  }
  return item.due_date ?? ''
}
