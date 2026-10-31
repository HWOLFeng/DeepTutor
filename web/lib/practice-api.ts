import { scopedUrl } from '@/lib/workspace-scope'
import { apiFetch, apiUrl } from '@/lib/api'
import type { NotebookEntry } from '@/lib/notebook-api'

const ROOT = '/api/question-notebook/practice'
export type PracticeRating = 'again' | 'hard' | 'good' | 'easy'
export interface PracticeSummary {
  total: number
  mistakes: number
  due: number
  overdue: number
  reviewed_today: number
  next_due_at: number | null
  day_end: number
  timezone: string
}
export interface PracticeRef {
  id: number
  content_workspace_id?: string
  content_workspace_name?: string
}
export interface PracticeQuestion {
  content_workspace_id?: string
  content_workspace_name?: string
  entry: NotebookEntry
  state: { version: number; review_count: number; is_mistake: boolean; due_at: number | null }
}
export type PracticeMetric = 'questions' | 'mistakes' | 'reviews'
export type PracticeCounts = Record<PracticeMetric, number>
export interface PracticeAnalytics {
  timezone: string
  days: number
  start_date: string
  end_date: string
  updated_at: number
  daily: (PracticeCounts & { date: string })[]
  sources: (PracticeCounts & { source: string })[]
  totals: PracticeCounts
}
export const getPracticeAnalytics = (days = 30) =>
  request<PracticeAnalytics>(`/analytics?${params()}&days=${days}`)
export interface ImportQuestion {
  question: string
  question_type: string
  options?: Record<string, string>
  correct_answer: string
  user_answer?: string
  explanation?: string
  tags: string[]
  question_images?: { id: string; url: string; filename: string; mime_type: string }[]
  /** Whether the learner's own answer already matches the correct one. */
  attempt?: 'correct' | 'incorrect' | 'unknown'
  /** Answer text the importer removed from a filled-in blank. */
  leaks?: string[]
  /** A second solve pass disagreed with the answer on this row. */
  answer_conflict?: boolean
  answer_source?: 'printed' | 'solved'
  page_type?: string
}
export interface ImportPreview {
  token: string | null
  total: number
  valid: number
  errors: { row: number; message: string }[]
  /** Every detected question; the learner unchecks the ones to leave out. */
  questions?: ImportQuestion[]
  samples: ImportQuestion[]
}
export class PracticeRequestError extends Error {
  constructor(
    message: string,
    public status: number
  ) {
    super(message)
  }
}
async function request<T>(path: string, init?: RequestInit, workspaceId?: string): Promise<T> {
  const response = await apiFetch(
    apiUrl(workspaceId === undefined ? `${ROOT}${path}` : scopedUrl(`${ROOT}${path}`, workspaceId)),
    { cache: 'no-store', ...init }
  )
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new PracticeRequestError(
      typeof body.detail === 'string' ? body.detail : `Request failed: ${response.status}`,
      response.status
    )
  }
  return response.json() as Promise<T>
}
const json = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})
function params(): string {
  return new URLSearchParams({
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
  }).toString()
}
export const getPracticeSummary = () =>
  request<PracticeSummary>(`/summary?${params()}`)
export const getPracticeQueue = () =>
  request<PracticeQuestion[]>(`/queue?${params()}`)
export const getPracticeQuestion = (id: number, workspaceId?: string) =>
  request<PracticeQuestion>(`/questions/${id}`, undefined, workspaceId)
export const checkPracticeAnswer = (id: number, answer: string, workspaceId?: string) =>
  request<{ correct: boolean | null }>(`/questions/${id}/check`, json({ answer }), workspaceId)
export const savePracticeReview = (
  id: number,
  body: {
    request_id: string
    version: number
    rating: PracticeRating
    answer: string
    self_report?: boolean
  },
  workspaceId?: string
) =>
  request<{ due_at: number; correct: boolean | null; rating: PracticeRating; is_mistake: boolean }>(
    `/questions/${id}/review`,
    json(body),
    workspaceId
  )
export function previewPracticeImport(
  files: File | readonly File[],
  target: 'bank' | 'mistakes',
  label?: string
) {
  const data = new FormData()
  const selected = Array.isArray(files) ? files : [files]
  if (selected.length > 1) {
    // Several question photos are one batch: the server stages them as a
    // single import so the sidebar shows one grouped row.
    selected.forEach(file => data.append('files', file, file.name))
  } else {
    data.append('file', selected[0])
  }
  data.append('target', target)
  if (label) data.append('label', label)
  return request<ImportPreview>('/import/preview', { method: 'POST', body: data })
}
export const commitPracticeImport = (token: string, selected?: number[]) =>
  request<{ created: number; duplicates: number; total: number; mistakes?: number }>(
    '/import/commit',
    json(selected ? { token, selected } : { token })
  )
export async function downloadPracticeTemplate(format: 'csv' | 'xlsx') {
  const response = await apiFetch(apiUrl(`${ROOT}/import/template?format=${format}`))
  if (!response.ok) throw new Error(`Request failed: ${response.status}`)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `practice-template.${format}`
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
