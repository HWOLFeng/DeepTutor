"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { practiceRoute } from "@/lib/learning-routes";
import { listNotebookEntries } from "@/lib/notebook-api";
import { PracticeSession } from "./PracticeSession";

/**
 * One practice set as a session: the sidebar's row starts it.
 *
 * The set is addressed by its `group_ref` (an imported file, a generated quiz
 * turn, a partner's recorded mistakes), so every kind of set behaves the same
 * way — which is what the learner expects from one click on a row that says
 * "12 questions".
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
  const [ids, setIds] = useState<number[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    // One import can hold up to 500 questions; the list API pages at 200.
    const load = async () => {
      const collected: number[] = [];
      for (let offset = 0; collected.length < 500; offset += 200) {
        const page = await listNotebookEntries({
          group: groupRef,
          limit: 200,
          offset,
          sort: "oldest",
        });
        collected.push(...page.items.map(item => item.id));
        if (page.items.length < 200) break;
      }
      if (alive) setIds(collected);
    };
    load().catch(err => {
      if (alive) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      alive = false;
    };
  }, [groupRef]);

  const back = () => router.replace(practiceRoute(new URLSearchParams(), ""));

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
  if (!ids) {
    return (
      <div className="flex min-h-[240px] items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  if (!ids.length) {
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
  return (
    <PracticeSession
      ids={ids}
      title={title}
      onClose={back}
      backLabel={t("Back to practice")}
    />
  );
}
