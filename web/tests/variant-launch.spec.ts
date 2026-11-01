import { expect, it, vi, beforeEach } from "vitest";

import { consumePendingQuizSeeds } from "@/lib/pending-quiz-seeds";
import type { QuizSeed } from "@/lib/quiz-types";
import {
  startVariantChat,
  variantAskFromCardMessage,
  variantAskMessage,
} from "@/lib/variant-launch";

const seed: QuizSeed = {
  question: "Solve 3x + 5 = 20.",
  question_type: "choice",
  options: { A: "5", B: "15" },
  correct_answer: "A",
  explanation: "3x = 15.",
  difficulty: "easy",
  topic: "一元一次方程",
};

beforeEach(() => {
  window.sessionStorage.clear();
});

it("hands the seeds and the ask to the composer, then opens the quiz chat", () => {
  const push = vi.fn();
  const opened = startVariantChat(
    [seed],
    variantAskMessage(1, key => key),
    push,
  );

  expect(opened).toBe(true);
  // The ask is a real user message: the backend uses it as the batch's topic.
  expect(push).toHaveBeenCalledTimes(1);
  expect(push.mock.calls[0][0]).toContain("/chat?capability=deep_question");
  // Seeds are consumed exactly once, by the composer this navigation opens.
  expect(consumePendingQuizSeeds()).toEqual([seed]);
  expect(consumePendingQuizSeeds()).toEqual([]);
});

it("refuses an empty selection instead of opening an empty chat", () => {
  const push = vi.fn();

  expect(startVariantChat([], "make some", push)).toBe(false);
  expect(startVariantChat([{ ...seed, question: "   " }], "make some", push)).toBe(
    false,
  );
  expect(push).not.toHaveBeenCalled();
  expect(consumePendingQuizSeeds()).toEqual([]);
});

it("writes the card's ask about one question in the learner's language", () => {
  const translate = (key: string, options?: Record<string, unknown>) =>
    `${key}|${options?.count}`;

  expect(variantAskFromCardMessage(3, translate)).toContain("|3");
  expect(variantAskMessage(2, translate)).toContain("|2");
});

it("keeps the workspace scope on the navigation target", () => {
  const push = vi.fn();
  window.history.replaceState({}, "", "/space/questions?dt_workspace=physics");

  startVariantChat([seed], "ask", push);

  expect(push.mock.calls[0][0]).toContain("dt_workspace=physics");
});
