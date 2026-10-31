"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { practiceRoute } from "@/lib/learning-routes";
import { listNotebookEntries } from "@/lib/notebook-api";
import { PracticeSession } from "./PracticeSession";

/** One imported file's questions as a set: the sidebar's group node starts it. */
export function PracticeGroupSession({
  materialId,
  file,
}: {
  materialId: string;
  file?: string;
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
          material_id: materialId,
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
  }, [materialId]);

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
      title={file}
      onClose={back}
      backLabel={t("Back to practice")}
    />
  );
}
