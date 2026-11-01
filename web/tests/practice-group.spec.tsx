import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { PracticePage } from "@/components/learning/practice/PracticePage";
import { initI18n } from "@/i18n/init";
import * as bank from "@/lib/notebook-api";
import { consumePendingQuizSeeds } from "@/lib/pending-quiz-seeds";

const replace = vi.fn();
const push = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/learning/practice",
  useSearchParams: () =>
    new URLSearchParams(
      "group=g%3Amaterial%7Cimport%3Aa&file=%E4%B8%89%E5%B9%B4%E7%BA%A7.docx"
    ),
  useRouter: () => ({ replace, push }),
}));
vi.mock("@/components/learning/practice/PracticeSession", () => ({
  PracticeSession: ({ ids, title }: { ids: number[]; title?: string }) => (
    <div data-testid="session">
      {ids.join(",")}|{title}
    </div>
  ),
}));
vi.mock("@/lib/notebook-api", async original => ({
  ...(await original<typeof bank>()),
  listNotebookEntries: vi.fn(),
  getSetCalibration: vi.fn(),
}));
vi.mock("@/lib/practice-api", async original => ({
  ...(await original<typeof import("@/lib/practice-api")>()),
}));

initI18n("en");

function entry(id: number) {
  return {
    id,
    question: `question ${id}`,
    question_type: "single_choice",
    options: { A: "1", B: "2" },
    correct_answer: "A",
    difficulty: "medium",
    material_title: "三年级.docx",
    categories: [],
  } as never;
}

beforeEach(() => {
  window.sessionStorage.clear();
  replace.mockClear();
  push.mockClear();
});

it("starts a practice session for the set the row names", async () => {
  vi.mocked(bank.listNotebookEntries).mockResolvedValue({
    total: 2,
    items: [entry(3), entry(1)],
  });
  vi.mocked(bank.getSetCalibration).mockResolvedValue({
    group_ref: "g:material|import:a",
    title: "三年级.docx",
    count: 2,
    answered: 0,
    correct: 0,
    accuracy: 0,
    label: "untouched",
    suggested_difficulty: "",
  });

  const { container } = render(<PracticePage />);
  await waitFor(() =>
    expect(screen.getByTestId("session").textContent).toBe("3,1|三年级.docx")
  );
  // The page shell is the scroll container; without it the card grows past the
  // viewport inside AppShell's clipped frame and its buttons become unreachable.
  expect(container.querySelector("[data-feature-page]")).not.toBeNull();
  // The set is addressed by its own identity, so an imported file, a generated
  // quiz and a partner's mistakes all reopen the same way.
  expect(vi.mocked(bank.listNotebookEntries)).toHaveBeenCalledWith(
    expect.objectContaining({ group: "g:material|import:a" })
  );
  // Nothing attempted yet: no calibration strip, because there is no evidence
  // to judge the set by.
  expect(screen.queryByTestId("set-calibration")).toBeNull();
});

it("says how the set is going and offers an easier round when it is not landing", async () => {
  const user = userEvent.setup();
  vi.mocked(bank.listNotebookEntries).mockResolvedValue({
    total: 4,
    items: [entry(1), entry(2), entry(3), entry(4)],
  });
  vi.mocked(bank.getSetCalibration).mockResolvedValue({
    group_ref: "g:material|import:a",
    title: "三年级.docx",
    count: 4,
    answered: 4,
    correct: 1,
    accuracy: 0.25,
    label: "too_hard",
    suggested_difficulty: "easy",
  });

  render(<PracticePage />);

  const strip = await screen.findByTestId("set-calibration");
  expect(strip).toHaveTextContent("Accuracy 25%");
  expect(strip).toHaveTextContent("(1/4 correct)");
  expect(strip).toHaveTextContent("Needs another round");

  await user.click(screen.getByRole("button", { name: "Practice an easier round" }));

  // The next batch is seeded by this set's own questions, one band lower, in
  // the quiz composer — the loop that closes "this set is too hard".
  expect(push).toHaveBeenCalledTimes(1);
  expect(push.mock.calls[0][0]).toContain("/chat?capability=deep_question");
  const seeds = consumePendingQuizSeeds();
  expect(seeds).toHaveLength(4);
  expect(seeds.every(seed => seed.difficulty === "easy")).toBe(true);
});
