/**
 * Hand a set of 举一反三 seeds from one surface to the chat composer.
 *
 * The learner picks questions in the bank (or on a quiz card), and the chat
 * that generates the variants is a *different* route, so the seeds have to
 * survive the navigation. They do not fit a query string: a seed carries its
 * options, answer and explanation, and a 5-seed batch would be a multi-kilobyte
 * URL that also leaks the learner's answers into history.
 *
 * sessionStorage, consumed exactly once, scoped to chat: a refresh must not
 * resurrect seeds the learner already used or cleared, and a later visit to the
 * composer must not silently generate against an old selection.
 */
import { browserStorage } from "@/shared/storage";
import type { QuizSeed } from "./quiz-types";

const PENDING_SEEDS_KEY = "deeptutor.pendingQuizSeeds.chat";

/** Store the seeds for the next chat composer to pick up. */
export function setPendingQuizSeeds(seeds: QuizSeed[]): void {
  if (typeof window === "undefined") return;
  const usable = seeds.filter((seed) => (seed.question ?? "").trim().length > 0);
  if (usable.length === 0) return;
  try {
    browserStorage.writeRaw("session", PENDING_SEEDS_KEY, JSON.stringify(usable));
  } catch {
    // Private-mode browsers reject sessionStorage; the learner still lands in
    // the composer, just without the pre-filled seeds.
  }
}

/** Read and clear the seeds. Returns `[]` when nothing is pending. */
export function consumePendingQuizSeeds(): QuizSeed[] {
  if (typeof window === "undefined") return [];
  try {
    const value = browserStorage.readRaw("session", PENDING_SEEDS_KEY);
    if (!value) return [];
    browserStorage.removeRaw("session", PENDING_SEEDS_KEY);
    const parsed = JSON.parse(value) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (seed): seed is QuizSeed =>
        Boolean(seed) &&
        typeof seed === "object" &&
        typeof (seed as QuizSeed).question === "string" &&
        (seed as QuizSeed).question.trim().length > 0,
    );
  } catch {
    return [];
  }
}
