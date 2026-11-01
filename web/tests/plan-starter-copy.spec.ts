import { describe, expect, it } from 'vitest'
import en from '@/locales/en/app.json'
import zh from '@/locales/zh/app.json'
import de from '@/locales/de/app.json'
import fr from '@/locales/fr/app.json'
import pl from '@/locales/pl/app.json'
import uk from '@/locales/uk/app.json'

/**
 * The plan starters are the only place most learners meet this feature, so the
 * copy is a product surface: it has to ask for a draft, and it has to name the
 * two things a plan lands in. These tests guard the promise the copy makes —
 * change the wording freely, but do not quietly drop it.
 */
const LOCALES = { de, en, fr, pl, uk, zh } as Record<string, Record<string, string>>

const PLAN_KEYS = [
  'starters.plan',
  'starters.plan.1',
  'starters.plan.1.prompt',
  'starters.plan.2',
  'starters.plan.2.prompt',
  'starters.plan.3',
  'starters.plan.3.prompt',
  'starters.plan.4',
  'starters.plan.4.prompt',
]

/** Per-locale words that must appear: draft, task, calendar. */
const MARKERS: Record<string, [RegExp, RegExp, RegExp]> = {
  en: [/draft/i, /task/i, /calendar/i],
  zh: [/草稿/, /任务/, /日历/],
  de: [/entwurf/i, /aufgabe/i, /kalender/i],
  fr: [/brouillon/i, /t[âa]che/i, /calendrier/i],
  pl: [/szkic/i, /zada[nń]/i, /kalendarz/i],
  uk: [/чернетк/i, /завдан/i, /календар/i],
}

describe('plan starter copy', () => {
  it('keeps exactly the same key set (user customisation is keyed on it)', () => {
    for (const [locale, catalog] of Object.entries(LOCALES)) {
      const present = Object.keys(catalog)
        .filter(key => key.startsWith('starters.plan'))
        .sort()
      expect(present, locale).toEqual([...PLAN_KEYS].sort())
    }
  })

  it('asks for tasks, a calendar and a draft in the two writing starters', () => {
    for (const [locale, catalog] of Object.entries(LOCALES)) {
      const [draft, task, calendar] = MARKERS[locale]
      for (const key of ['starters.plan.1.prompt', 'starters.plan.3.prompt']) {
        const prompt = catalog[key]
        expect(prompt, `${locale} ${key} draft`).toMatch(draft)
        expect(prompt, `${locale} ${key} task`).toMatch(task)
        expect(prompt, `${locale} ${key} calendar`).toMatch(calendar)
      }
    }
  })

  it('offers the same route from the two non-writing starters', () => {
    for (const [locale, catalog] of Object.entries(LOCALES)) {
      const [draft, task, calendar] = MARKERS[locale]
      for (const key of ['starters.plan.2.prompt', 'starters.plan.4.prompt']) {
        const prompt = catalog[key]
        expect(prompt, `${locale} ${key}`).toMatch(draft)
        expect(prompt, `${locale} ${key}`).toMatch(task)
        expect(prompt, `${locale} ${key}`).toMatch(calendar)
      }
    }
  })

  it('still tells the model to ask before assuming', () => {
    for (const [locale, catalog] of Object.entries(LOCALES)) {
      const prompt = catalog['starters.plan.1.prompt']
      const asking = {
        en: /ask/i,
        zh: /问我|先问/,
        de: /frag/i,
        fr: /demande/i,
        pl: /zapytaj/i,
        uk: /спитай/i,
      }[locale] as RegExp
      expect(prompt, locale).toMatch(asking)
    }
  })

  it('does not promise what the card will not do', () => {
    for (const [locale, catalog] of Object.entries(LOCALES)) {
      for (const key of ['starters.plan.1.prompt', 'starters.plan.3.prompt']) {
        // No "I will automatically…" promises: writing is the learner's click.
        expect(catalog[key], `${locale} ${key}`).not.toMatch(
          /automatically|自动(帮你)?(创建|写入|建好)/
        )
      }
    }
  })
})
