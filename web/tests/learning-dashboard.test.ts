import assert from 'node:assert/strict'
import test from 'node:test'
import {
  allLearningActivity,
  learningSurfaceStats,
  recentLearningActivity,
  learningTodos,
  isWatchingSession,
  type LearningSources,
} from '../lib/learning-dashboard'
import type { MasteryTopic } from '../lib/learning-api'
import type { Book } from '../lib/book-types'
import type { SessionSummary } from '../lib/session-api'

const topic = (status: 'active' | 'archived', due: boolean) =>
  ({
    path_id: status,
    name: 'Home',
    metadata: { status, goal: 'Home' },
    updated_at: 80,
    map: { counts: { mastered: 1, total: 2 } },
    reviews: [{ due }],
  }) as MasteryTopic
const sources: LearningSources = {
  books: [
    {
      id: 'b',
      title: 'Settings',
      updated_at: 60,
      status: 'paused',
      metadata: { pause_reason: 'provider' },
    },
    { id: 'c', title: 'c', updated_at: 100, status: 'compiling', metadata: {} },
    { id: 'd', title: 'd', updated_at: 10, status: 'ready', metadata: {} },
  ] as Book[],
  mastery: [topic('active', true), topic('archived', true)],
  reading: [
    {
      workspace_id: 'r',
      title: 'Memory',
      updated_at: 70,
      created_at: 1,
      description: '',
      tabs: [],
      active_material_id: null,
    },
  ],
  watching: [
    {
      session_id: 's',
      title: 'Home',
      updated_at: 90,
      preferences: { workspace_mode: 'immersive_watching' },
    },
    {
      session_id: 's2',
      title: 'legacy',
      updated_at: 30,
      preferences: { capability: 'immersive_watching' },
    },
  ] as SessionSummary[],
}

test('recent learning mixes four sources by epoch seconds and keeps six', () => {
  const recent = recentLearningActivity(sources)
  assert.deepEqual(
    recent.map(item => item.updatedAt),
    [100, 90, 80, 70, 60, 30]
  )
  assert.equal(recent[1].href, '/learning/watching/s?dt_workspace=')
  assert.equal(recent[2].title, 'Home')
  assert.equal(recent[2].progress, 0.5)
  assert.equal(recent[3].title, 'Memory')
  assert.equal(recent[4].title, 'Settings')
})

test('a practice set opens as that set, whatever produced it', () => {
  const withPractice: LearningSources = {
    ...sources,
    practice: [
      {
        id: 9,
        group_ref: 'g:material|import:abc',
        title: '三年级语文修改病句.docx',
        count: 4,
        material_id: 'import:abc',
        created_at: 5,
        updated_at: 6,
      },
    ],
  }
  const row = allLearningActivity(withPractice).find(item => item.kind === 'practice')
  assert.ok(row)
  assert.equal(row.title, '三年级语文修改病句.docx')
  const link = new URL(row.href, 'http://workspace.local')
  assert.equal(link.pathname, '/learning/practice')
  assert.equal(link.searchParams.get('group'), 'g:material|import:abc')
  assert.equal(link.searchParams.get('file'), '三年级语文修改病句.docx')
})

test('a generated quiz set opens like an imported file, not the practice hub', () => {
  const withPractice: LearningSources = {
    ...sources,
    practice: [
      {
        id: 21,
        group_ref: 'g:material|quiz:turn_A',
        title: 'Projection · variants',
        count: 5,
        material_id: 'quiz:turn_A',
        created_at: 5,
        updated_at: 6,
      },
    ],
  }
  const row = allLearningActivity(withPractice).find(item => item.kind === 'practice')
  assert.ok(row)
  const link = new URL(row.href, 'http://workspace.local')
  assert.equal(link.searchParams.get('group'), 'g:material|quiz:turn_A')
  assert.equal(link.searchParams.get('file'), 'Projection · variants')
})

test('two sets with the same name stay two rows', () => {
  const withPractice: LearningSources = {
    ...sources,
    practice: [
      {
        id: 1,
        group_ref: 'g:material|partner:p1:a',
        title: 'Study Buddy',
        count: 2,
        material_id: 'partner:p1:a',
        created_at: 5,
        updated_at: 6,
      },
      {
        id: 2,
        group_ref: 'g:material|partner:p2:b',
        title: 'Study Buddy',
        count: 3,
        material_id: 'partner:p2:b',
        created_at: 7,
        updated_at: 8,
      },
    ],
  }
  const rows = allLearningActivity(withPractice).filter(item => item.kind === 'practice')
  assert.deepEqual(
    rows.map(row => row.key),
    ['practice::g:material|partner:p2:b', 'practice::g:material|partner:p1:a']
  )
  assert.deepEqual(
    rows.map(row => row.title),
    ['Study Buddy', 'Study Buddy']
  )
})

test('a single imported question still opens that question', () => {
  const withPractice: LearningSources = {
    ...sources,
    practice: [
      {
        id: 12,
        group_ref: 'g:material|import:def',
        title: '错题.pdf',
        count: 1,
        material_id: 'import:def',
        created_at: 5,
        updated_at: 6,
      },
    ],
  }
  const row = allLearningActivity(withPractice).find(item => item.kind === 'practice')
  assert.ok(row)
  assert.equal(row.href, '/learning/practice?question=12&dt_workspace=')
})

test('todos contain due active topics and paused or compiling books only', () => {
  const todos = learningTodos(sources)
  assert.deepEqual(
    todos.reviews.map(item => item.path_id),
    ['active']
  )
  assert.deepEqual(
    todos.books.map(item => item.id),
    ['b', 'c']
  )
  assert.equal(learningTodos({ ...sources, mastery: [topic('active', false)] }).reviews.length, 0)
})

test('watching accepts legacy preferences without claiming ordinary chats', () => {
  assert.equal(isWatchingSession(sources.watching[1]), true)
  assert.equal(isWatchingSession({ preferences: { capability: 'chat' } } as SessionSummary), false)
})

test('surface stats count each kind and name the piece of it touched last', () => {
  const stats = learningSurfaceStats(sources)
  assert.deepEqual(
    Object.fromEntries(Object.entries(stats).map(([kind, stat]) => [kind, stat.count])),
    { books: 3, mastery: 1, reading: 1, watching: 2, practice: 0 }
  )
  assert.equal(stats.books.latest?.title, 'c')
  assert.equal(stats.watching.latest?.updatedAt, 90)
  // Archived paths are not work in progress, so they are neither counted nor shown.
  assert.equal(stats.mastery.latest?.href, '/learning/mastery/active?dt_workspace=')
  const empty = learningSurfaceStats({ books: [], mastery: [], reading: [], watching: [] })
  assert.equal(empty.reading.count, 0)
  assert.equal(empty.reading.latest, undefined)
})
