/**
 * Turning picked questions into 举一反三 seeds.
 *
 * A seed is not the question the learner will answer — it is the *shape* the
 * generator must keep (same knowledge point, type, difficulty) while changing
 * the surface. Keeping this mapping in one place means the bank and the quiz
 * card produce identical seeds, and the backend only ever sees one shape.
 */
import type { NotebookEntry } from "./notebook-api";
import { normalizeQuizQuestionType } from "./quiz-question-type";
import type { QuizSeed } from "./quiz-types";

/**
 * How many questions may seed one variant batch.
 *
 * Above this the request stops being "more like these questions" and becomes
 * "generate a quiz about this topic" — a different product action with a
 * different config card.
 */
export const MAX_VARIANT_SEEDS = 5;

/** Map bank entries onto the seed shape `deep_question` accepts. */
export function buildQuizSeeds(entries: NotebookEntry[]): QuizSeed[] {
  return entries
    .filter((entry) => (entry.question ?? "").trim().length > 0)
    .slice(0, MAX_VARIANT_SEEDS)
    .map((entry) => ({
      question: entry.question.trim(),
      question_type: normalizeQuizQuestionType(entry.question_type),
      options: entry.options ?? {},
      correct_answer: entry.correct_answer ?? "",
      explanation: entry.explanation ?? "",
      difficulty: entry.difficulty ?? "",
      // The tag names the set better than a file name does; the material is
      // the next-best label when the learner never filed the question.
      topic:
        entry.categories?.[0]?.name?.trim() ||
        entry.material_title?.trim() ||
        "",
    }));
}

/** Whether a selection can seed a batch (and how many of it will be used). */
export function variantSeedSelection(entries: NotebookEntry[]): {
  seeds: QuizSeed[];
  count: number;
  tooMany: boolean;
} {
  const usable = entries.filter((entry) => (entry.question ?? "").trim().length > 0);
  return {
    seeds: buildQuizSeeds(usable),
    count: usable.length,
    tooMany: usable.length > MAX_VARIANT_SEEDS,
  };
}
