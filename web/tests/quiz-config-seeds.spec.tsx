import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import QuizConfigPanel from "@/components/quiz/QuizConfigPanel";
import { initI18n } from "@/i18n/init";
import { DEFAULT_QUIZ_CONFIG, type DeepQuestionFormConfig } from "@/lib/quiz-types";

initI18n("en");

function seededConfig(): DeepQuestionFormConfig {
  return {
    ...DEFAULT_QUIZ_CONFIG,
    num_questions: 2,
    seeds: [
      {
        question: "Solve 3x + 5 = 20.",
        question_type: "choice",
        options: { A: "5", B: "15" },
        correct_answer: "A",
        topic: "一元一次方程",
      },
      {
        question: "Solve 4x - 7 = 21.",
        question_type: "short_answer",
        correct_answer: "7",
        topic: "一元一次方程（进阶）",
      },
    ],
  };
}

it("shows the seeds so the learner knows what the batch varies", () => {
  render(
    <QuizConfigPanel
      value={seededConfig()}
      onChange={vi.fn()}
      uploadedPdf={null}
      onUploadPdf={vi.fn()}
    />,
  );

  expect(screen.getByText("Seeds (2)")).toBeVisible();
  // The topic names the seed; the raw stem is the fallback, not the label here.
  expect(screen.getByText("一元一次方程")).toBeVisible();
  expect(screen.getByText("一元一次方程（进阶）")).toBeVisible();
  // The count is the learner's, not the default 3.
  expect(screen.getByDisplayValue("2")).toBeVisible();
});

it("removes one seed and clears the rest without touching the counts", () => {
  const onChange = vi.fn();
  render(
    <QuizConfigPanel
      value={seededConfig()}
      onChange={onChange}
      uploadedPdf={null}
      onUploadPdf={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Remove seed 1" }));

  const afterRemove = onChange.mock.calls.at(-1)?.[0] as DeepQuestionFormConfig;
  expect(afterRemove.seeds.map(seed => seed.question)).toEqual([
    "Solve 4x - 7 = 21.",
  ]);
  expect(afterRemove.num_questions).toBe(2);

  onChange.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "Clear" }));

  const afterClear = onChange.mock.calls.at(-1)?.[0] as DeepQuestionFormConfig;
  expect(afterClear.seeds).toEqual([]);
  // Clearing the seeds returns the batch to topic-driven generation; the
  // remaining config must survive so the learner does not lose their work.
  expect(afterClear.num_questions).toBe(2);
  expect(afterClear.mode).toBe("custom");
});
