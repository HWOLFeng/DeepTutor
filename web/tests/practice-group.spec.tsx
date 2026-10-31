import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { PracticePage } from "@/components/learning/practice/PracticePage";
import { initI18n } from "@/i18n/init";
import * as bank from "@/lib/notebook-api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/learning/practice",
  useSearchParams: () => new URLSearchParams("group=import%3Aa&file=%E4%B8%89%E5%B9%B4%E7%BA%A7.docx"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
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
}));
vi.mock("@/lib/practice-api", async original => ({
  ...(await original<typeof import("@/lib/practice-api")>()),
}));

initI18n("en");

it("starts a practice session for the grouped file", async () => {
  vi.mocked(bank.listNotebookEntries).mockResolvedValue({
    total: 2,
    items: [{ id: 3 }, { id: 1 }] as never,
  });
  const { container } = render(<PracticePage />);
  await waitFor(() =>
    expect(screen.getByTestId("session").textContent).toBe("3,1|三年级.docx")
  );
  // The page shell is the scroll container; without it the card grows past the
  // viewport inside AppShell's clipped frame and its buttons become unreachable.
  expect(container.querySelector("[data-feature-page]")).not.toBeNull();
  expect(vi.mocked(bank.listNotebookEntries)).toHaveBeenCalledWith(
    expect.objectContaining({ material_id: "import:a" })
  );
});
