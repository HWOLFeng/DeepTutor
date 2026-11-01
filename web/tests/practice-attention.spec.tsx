import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ReviewHome } from "@/components/learning/practice/ReviewHome";
import { initI18n } from "@/i18n/init";
import * as practice from "@/lib/practice-api";
import * as bank from "@/lib/notebook-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/learning/practice",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
vi.mock("@/components/learning/practice/PracticeInsights", () => ({
  PracticeInsights: () => <div data-testid="insights" />,
}));
vi.mock("@/lib/practice-api", async original => ({
  ...(await original<typeof practice>()),
  getPracticeSummary: vi.fn(),
  getPracticeQueue: vi.fn(),
}));
vi.mock("@/lib/notebook-api", async original => ({
  ...(await original<typeof bank>()),
  getSetsNeedingPractice: vi.fn(),
}));

initI18n("en");

it("reminds the learner about a set that is not landing, and links into it", async () => {
  vi.mocked(practice.getPracticeSummary).mockResolvedValue({
    due: 0,
    reviewed_today: 0,
    total: 12,
  } as never);
  vi.mocked(practice.getPracticeQueue).mockResolvedValue([]);
  vi.mocked(bank.getSetsNeedingPractice).mockResolvedValue([
    {
      group_ref: "g:material|quiz:turn_A",
      title: "Projection · variants",
      count: 5,
      answered: 5,
      correct: 1,
      accuracy: 0.2,
      label: "too_hard",
      suggested_difficulty: "easy",
    },
  ]);

  render(<ReviewHome />);

  // The reminder is separate from today's review count: being caught up does
  // not mean the last batch actually landed.
  expect(
    await screen.findByText("Sets that need another round")
  ).toBeInTheDocument();
  expect(await screen.findByText("Projection · variants")).toBeInTheDocument();
  expect(screen.getByText("Accuracy 20%")).toBeInTheDocument();
  const link = screen.getByRole("link", { name: /Practice another round/ });
  // Opening the set is where the "easier round" action lives; the reminder
  // must not generate anything by itself.
  expect(link.getAttribute("href")).toContain(
    "group=g%3Amaterial%7Cquiz%3Aturn_A"
  );
});

it("stays quiet when every set is doing fine", async () => {
  vi.mocked(practice.getPracticeSummary).mockResolvedValue({
    due: 3,
    reviewed_today: 1,
    total: 12,
  } as never);
  vi.mocked(practice.getPracticeQueue).mockResolvedValue([]);
  vi.mocked(bank.getSetsNeedingPractice).mockResolvedValue([]);

  render(<ReviewHome />);

  expect(await screen.findByText("Today's review")).toBeInTheDocument();
  expect(screen.queryByText("Sets that need another round")).toBeNull();
});
