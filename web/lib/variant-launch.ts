/**
 * Start a 举一反三 batch from a surface that is not the composer.
 *
 * The learner picks questions in the bank or on a quiz card; the generation
 * itself happens in the chat composer (`deep_question`), which is a different
 * route. Getting there needs three things at once — the seeds, an opening ask,
 * and the capability — and doing them in one place keeps the two entry points
 * from drifting (one of them silently losing the seeds was the bug this
 * prevents).
 *
 * The ask is written as the learner's own message, in their UI language: the
 * backend treats the message as the topic of the batch, so it must read like a
 * request, not like an internal instruction.
 */
import { setPendingPrompt } from "./pending-prompt";
import { setPendingQuizSeeds } from "./pending-quiz-seeds";
import type { QuizSeed } from "./quiz-types";
import { scopedUrl } from "./workspace-scope";

/** The chat route that hosts the quiz composer. */
export const VARIANT_CHAT_ROUTE = "/chat?capability=deep_question";

/**
 * Hand `seeds` to the chat composer and navigate there.
 *
 * Returns `false` (and navigates nowhere) when there is nothing to seed, so a
 * caller cannot turn an empty selection into an empty chat.
 */
export function startVariantChat(
  seeds: QuizSeed[],
  message: string,
  push: (href: string) => void,
): boolean {
  const usable = seeds.filter((seed) => (seed.question ?? "").trim().length > 0);
  if (usable.length === 0) return false;
  setPendingQuizSeeds(usable);
  setPendingPrompt(message);
  push(scopedUrl(VARIANT_CHAT_ROUTE));
  return true;
}

/** The opening ask for a batch seeded by `count` picked questions. */
export function variantAskMessage(
  count: number,
  translate: (key: string, options?: Record<string, unknown>) => string,
): string {
  return translate(
    "Make new questions like the {{count}} I picked: same knowledge points and types, one step harder, and name the set after what it practises.",
    { count },
  );
}

/** The opening ask when the seed is the single question a card is showing. */
export function variantAskFromCardMessage(
  count: number,
  translate: (key: string, options?: Record<string, unknown>) => string,
): string {
  return translate(
    "Make {{count}} new questions like this one: same knowledge point and type, a little harder, and name the set after what it practises.",
    { count },
  );
}
