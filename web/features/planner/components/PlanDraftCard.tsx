'use client'

import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Lock } from 'lucide-react'
import {
  applyDraft,
  type ApplyOutcome,
  type Decision,
  type ItemKind,
  type PlanDraft,
  type PlanItem,
} from '@/lib/planner-api'
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

  const readOnly = draft.status !== 'drafting' && draft.status !== 'reviewing'
  const byId = useMemo(
    () => new Map(draft.items.map(item => [item.item_id, item])),
    [draft.items]
  )

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
  }

  const selectable = draft.items.filter(item => !item.locked && !readOnly)
  const selected = draft.items.filter(isSelected)
  const total = draft.items.length
  const minutes = selected.reduce((sum, item) => sum + (item.duration_min ?? 0), 0)
  const hours = (minutes / 60).toFixed(1)
  const limit = draft.summary.weekly_limit_min
  const overBudget = typeof limit === 'number' && minutes > limit

  async function confirm() {
    if (busy || !selected.length) return
    setBusy(true)
    setError(null)
    try {
      const decisions: Record<string, Decision> = {}
      for (const item of draft.items) {
        decisions[item.item_id] = isSelected(item) ? 'accepted' : 'dismissed'
      }
      const outcome = await applyDraft(draft.id, decisions)
      onApplied?.(outcome)
    } catch (failure) {
      // Keep the selection: retrying must not cost the learner their choices.
      setError(failure instanceof Error ? failure.message : t('planner.draft.error'))
    } finally {
      setBusy(false)
    }
  }

  const groups: Array<{ kind: ItemKind; label: string }> = [
    { kind: 'task', label: t('planner.draft.group.tasks', { n: countOf(draft, 'task') }) },
    { kind: 'block', label: t('planner.draft.group.blocks', { n: countOf(draft, 'block') }) },
    { kind: 'review', label: t('planner.draft.group.reviews', { n: countOf(draft, 'review') }) },
  ]

  return (
    <section className="planner-draft" aria-label={t('planner.draft.title')}>
      <header className="planner-draft__head">
        <div>
          <h3>{t('planner.draft.title')}</h3>
          <p className="planner-draft__sub">
            {draft.summary.goal ? `${draft.summary.goal} · ` : ''}
            {readOnly ? t(`planner.draft.status.${draft.status}`) : t('planner.draft.unsaved')}
          </p>
        </div>
      </header>

      {groups.map(group =>
        countOf(draft, group.kind) === 0 ? null : (
          <div key={group.kind} className="planner-draft__group">
            <h4>{group.label}</h4>
            <ul>
              {draft.items
                .filter(item => item.kind === group.kind)
                .map(item => (
                  <li key={item.item_id} className="planner-draft__row">
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
                      {when(item)}
                      {item.duration_min ? ` · ${item.duration_min}min` : ''}
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
                    </span>
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
          <span className="planner-draft__count">{t('planner.draft.selected', { n: selected.length, total, hours })}</span>
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
            <button
              type="button"
              className="planner-draft__confirm"
              onClick={() => void confirm()}
              disabled={busy || selected.length === 0}
              title={selected.length === 0 ? t('planner.draft.atLeastOne') : undefined}
            >
              {t('planner.draft.confirm', { n: selected.length })}
            </button>
          </div>
        </footer>
      )}
    </section>
  )
}

function countOf(draft: PlanDraft, kind: ItemKind): number {
  return draft.items.filter(item => item.kind === kind).length
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
