"use client";

import { useTranslation } from "react-i18next";
import { CheckSquare, FolderMinus, Sparkles, X } from "lucide-react";
import type { NotebookCategory } from "@/lib/notebook-api";
import CategoryMenu from "./CategoryMenu";
import type { BankScope } from "./useQuestionBank";
import Tooltip from "@/shared/ui/Tooltip";

interface BankSelectionBarProps {
  count: number;
  visibleCount: number;
  scope: BankScope;
  categories: NotebookCategory[];
  onSelectAll: () => void;
  onClear: () => void;
  onFile: (categoryId: number) => Promise<boolean>;
  onCreateAndFile: (name: string) => Promise<boolean>;
  onUnfileFromCurrent: () => Promise<boolean>;
  /**
   * Start 举一反三 with the selected questions. Omitted on surfaces that cannot
   * open the quiz composer, so the bar never offers an action that goes nowhere.
   */
  onGenerateVariants?: () => void;
  /** How many selected questions may seed one batch (the action caps at this). */
  variantSeedLimit?: number;
}

/**
 * Floating bar for acting on a multi-selection.
 *
 * Appears only once something is selected, so the default reading view
 * stays clean; "remove from this category" is offered only while actually
 * inside a category, where it is the one unambiguous meaning.
 */
export default function BankSelectionBar({
  count,
  visibleCount,
  scope,
  categories,
  onSelectAll,
  onClear,
  onFile,
  onCreateAndFile,
  onUnfileFromCurrent,
  onGenerateVariants,
  variantSeedLimit,
}: BankSelectionBarProps) {
  const { t } = useTranslation();
  if (count === 0) return null;
  const tooManySeeds =
    onGenerateVariants !== undefined &&
    variantSeedLimit !== undefined &&
    count > variantSeedLimit;

  return (
    <div className="sticky bottom-4 z-20 mx-auto flex w-fit max-w-full flex-wrap items-center gap-2 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3 py-2 shadow-lg">
      <span className="text-[12.5px] font-medium text-[var(--foreground)]">
        {count} {t("selected")}
      </span>

      {count < visibleCount && (
        <button
          type="button"
          onClick={onSelectAll}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[12px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <CheckSquare className="h-3.5 w-3.5" />
          {t("Select all")}
        </button>
      )}

      <span className="h-4 w-px bg-[var(--border)]" />

      {onGenerateVariants && (
        <Tooltip
          label={
            tooManySeeds
              ? t("Pick at most {{count}} questions to seed a variant set.", {
                  count: variantSeedLimit ?? 0,
                })
              : t("Write new questions like these, as one named practice set.")
          }
          side="top"
        >
          <button
            type="button"
            disabled={tooManySeeds}
            onClick={onGenerateVariants}
            className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] bg-[var(--card)] px-2.5 py-1.5 text-[12px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/60 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {t("Similar questions")}
          </button>
        </Tooltip>
      )}

      <CategoryMenu
        categories={categories}
        label={t("Add tag")}
        align="left"
        direction="up"
        variant="outlined"
        onPick={onFile}
        onCreate={onCreateAndFile}
      />

      {scope.kind === "category" && (
        <button
          type="button"
          onClick={() => void onUnfileFromCurrent()}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-2 py-1.5 text-[11.5px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <FolderMinus className="h-3.5 w-3.5" />
          {t("Remove tag")}
        </button>
      )}

      <Tooltip label={t("Clear selection")} side="top">
        <button
          type="button"
          onClick={onClear}
          aria-label={t("Clear selection")}
          className="rounded-lg p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </Tooltip>
    </div>
  );
}
