'use client'

import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { getDraft, type PlanDraft } from '@/lib/planner-api'
import PlanDraftCard from './PlanDraftCard'

/**
 * Loads the draft a turn produced and hands it to the review card.
 *
 * The stream carries an id, not the rows, so the card always reflects the
 * server's current truth: a draft someone already accepted opens read-only,
 * and one that was cleaned up after its TTL says so in one line instead of
 * failing the whole message.
 *
 * A missing draft is an expected state, not an error — drafts expire, and a
 * conversation can be replayed weeks later.
 */
export default function PlanDraftPanel({ draftId }: { draftId: string }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<PlanDraft | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'missing'>('loading')

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()
    setState('loading')
    getDraft(draftId, controller.signal)
      .then(loaded => {
        if (cancelled) return
        setDraft(loaded)
        setState('ready')
      })
      .catch(() => {
        if (cancelled) return
        // No retry: an expired draft will not become readable again, and a
        // reload loop in a transcript is worse than one honest line.
        setState('missing')
      })
    return () => {
      cancelled = true
      controller.abort()
    }
  }, [draftId])

  if (state === 'missing') {
    return (
      <p className="planner-draft__missing" role="status">
        {t('planner.draft.missing')}
      </p>
    )
  }
  if (state === 'loading' || !draft) {
    return (
      <p className="planner-draft__missing" role="status">
        {t('planner.draft.loading')}
      </p>
    )
  }
  return <PlanDraftCard draft={draft} />
}
