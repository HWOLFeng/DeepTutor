"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Loader2, RotateCcw } from "lucide-react";
import { practiceRoute } from "@/lib/learning-routes";
import {
  getSetCalibration,
  listNotebookEntries,
  type NotebookEntry,
  type SetCalibration,
} from "@/lib/notebook-api";
import { buildQuizSeeds, withSeedDifficulty } from "@/lib/quiz-seeds";
import { startVariantChat } from "@/lib/variant-launch";
import { PracticeSession } from "./PracticeSession";

/** What the next seeded batch should be, given how this set went. */
function nextRoundPlan(
  calibration: SetCalibration | null,
): { difficulty: string; messageKey: string } {
  switch (calibration?.label) {
    case "too_hard":
      return {
        difficulty: calibration.suggested_difficulty || "easy",
        messageKey:
          "Make an easier round of variants from these questions: same knowledge points and types, one level down, and name the set after what it practises.",
      };
    case "too_easy":
      return {
        difficulty: calibration.suggested_difficulty || "hard",
        messageKey:
          "Make a harder round of variants from these questions: same knowledge points and types, one level up, and name the set after what it practises.",
      };
    default:
      return {
        difficulty: "",
        messageKey:
          "Make another round of variants from these questions: same knowledge points and types, and name the set after what it practises.",
      };
  }
}

/**
 * One practice set as a session: the sidebar's row starts it.
 *
 * The set is addressed by its `group_ref` (an imported file, a generated quiz
 * turn, a partner's recorded mistakes), so every kind of set behaves the same
 * way — which is what the learner expects from one click on a row that says
 * "12 questions".
 *
 * Above the session sits the set's calibration: how many of its questions have
 * been attempted, how many were right, and — when it is not landing — a button
 * that starts an easier seeded round. That is the feedback loop the phase-1
 * sets needed: a batch the learner keeps failing must not be re-generated
 * unchanged.
 */
export function PracticeGroupSession({
  groupRef,
  title = "",
}: {
  groupRef: string;
  title?: string;
}) {
  const { t } = useTranslation();
  const router = useRouter();
  const [entries, setEntries] = useState<NotebookEntry[] | null>(null);
  const [calibration, setCalibration] = useState<SetCalibration | null>(null);
  const [error, setError] = useState("");
  const ids = useMemo(() => (entries ?? []).map((entry) => entry.id), [entries]);

  useEffect(() => {
    let alive = true;
    // One import can hold up to 500 questions; the list API pages at 200.
    const load = async () => {
      const collected: NotebookEntry[] = [];
      for (let offset = 0; collected.length < 500; offset += 200) {
        const page = await listNotebookEntries({
          group: groupRef,
          limit: 200,
          offset,
          sort: "oldest",
        });
        collected.push(...page.items);
        if (page.items.length < 200) break;
      }
      if (alive) setEntries(collected);
    };
    load().catch(err => {
      if (alive) setError(err instanceof Error ? err.message : String(err));
    });
    // The calibration is a second, small read: a failure here must not stop the
    // learner from practising the set.
    void getSetCalibration(groupRef)
      .then(result => {
        if (alive) setCalibration(result);
      })
      .catch(() => {
        if (alive) setCalibration(null);
      });
    return () => {
      alive = false;
    };
  }, [groupRef]);

  const back = () => router.replace(practiceRoute(new URLSearchParams(), ""));

  const practiceAnotherRound = () => {
    const seeds = buildQuizSeeds(entries ?? []);
    if (!seeds.length) return;
    const plan = nextRoundPlan(calibration);
    startVariantChat(
      withSeedDifficulty(seeds, plan.difficulty),
      t(plan.messageKey),
      href => router.push(href),
    );
  };

  if (error) {
    return (
      <div className="mx-auto max-w-3xl rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
        <p>{t("Could not start this practice set: {{error}}", { error })}</p>
        <button
          type="button"
          onClick={back}
          className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
        >
          {t("Back to practice")}
        </button>
      </div>
    );
  }
  if (!entries) {
    return (
      <div className="flex min-h-[240px] items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!entries.length) {
    return (
      <div className="mx-auto max-w-3xl rounded-xl border border-border bg-card p-6 text-sm">
        <p>{t("This file has no questions left to practice.")}</p>
        <button
          type="button"
          onClick={back}
          className="mt-3 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
        >
          {t("Back to practice")}
        </button>
      </div>
    );
  }
  const measured = calibration !== null && calibration.answered > 0;
  const percent = calibration ? Math.round(calibration.accuracy * 100) : 0;
  return (
    <div className="mx-auto max-w-3xl space-y-3">
      {measured && calibration ? (
        <div
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 text-sm"
          data-testid="set-calibration"
        >
          <p className="min-w-0">
            <span className="font-medium">
              {t("Accuracy {{percent}}%", { percent })}
            </span>{' '}
            <span className="text-muted-foreground">
              {t("({{correct}}/{{answered}} correct)", {
                correct: calibration.correct,
                answered: calibration.answered,
              })}
            </span>
            {calibration.label === "too_hard" ? (
              <span className="ml-2 rounded-full bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                {t("Needs another round")}
              </span>
            ) : null}
          </p>
          <button
            type="button"
            onClick={practiceAnotherRound}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted"
          >
            <RotateCcw size={13} />
            {calibration.label === "too_hard"
              ? t("Practice an easier round")
              : calibration.label === "too_easy"
                ? t("Practice a harder round")
                : t("Practice another round")}
          </button>
        </div>
      ) : null}
      <PracticeSession
        ids={ids}
        title={title}
        onClose={back}
        backLabel={t("Back to practice")}
      />
    </div>
  );
}
