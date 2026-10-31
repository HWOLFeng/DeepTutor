"use client";

import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Download, FileSpreadsheet, Loader2, X } from "lucide-react";
import {
  commitPracticeImport,
  downloadPracticeTemplate,
  previewPracticeImport,
  type ImportPreview,
} from "@/lib/practice-api";
import { QuestionFigures } from "@/components/common/QuestionFigures";

/** Phone photos are several megabytes each; shrink them before upload so a
 *  batch of screenshots still fits the request, without losing legibility.
 *  WebP at a visually-lossless quality keeps the server from re-encoding the
 *  image a second time, so the photo is only compressed once. */
async function shrinkQuestionImage(file: File): Promise<File> {
  if (!file.type.startsWith("image/") || file.size <= 900_000) return file;
  // Only camera photos are re-encoded here. PNG screenshots and diagrams stay
  // byte-for-byte and are compressed losslessly by the server, so a figure's
  // text never goes through two lossy passes.
  if (file.type !== "image/jpeg") return file;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    if (scale === 1) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    if (!context) return file;
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const webp = await new Promise<Blob | null>(resolve =>
      canvas.toBlob(resolve, "image/webp", 0.95)
    );
    const blob =
      webp && webp.type === "image/webp"
        ? webp
        : await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) return file;
    const extension = blob.type === "image/webp" ? "webp" : blob.type === "image/png" ? "png" : "jpg";
    return new File([blob], `${file.name.replace(/\.[^.]+$/, "")}.${extension}`, {
      type: blob.type,
    });
  } catch {
    return file;
  }
}

export function PracticeImport({
  onClose,
  onImported,
  initialTarget,
}: {
  onClose: () => void;
  onImported: (message: string) => void;
  initialTarget: "bank" | "mistakes";
}) {
  const { t } = useTranslation();
  const [target, setTarget] = useState(initialTarget);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  // OCR is probabilistic, so every detected question is offered as a choice.
  // All of them start checked; the learner unchecks what came out wrong.
  const rows = preview ? preview.questions ?? preview.samples : [];
  // Answers the model worked out itself are guesses; the printed ones are not.
  const solvedCount = rows.filter(row => row.answer_source === "solved").length;
  const allSelected = rows.length > 0 && selected.size === rows.length;
  function toggleRow(index: number) {
    setSelected(current => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }
  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((_, index) => index)));
  }
  async function select(files?: FileList | null) {
    const chosen = Array.from(files ?? []);
    if (!chosen.length || pending.current) return;
    const multi = chosen.length > 1;
    if (multi && chosen.some(file => !file.type.startsWith("image/"))) {
      setError(
        t("Choose several images, or one spreadsheet, PDF or DOCX file at a time.")
      );
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    setPreview(null);
    const label = multi
      ? t("{{count}} images: {{name}}", {
          name: chosen[0].name,
          count: chosen.length,
        })
      : chosen[0].name;
    setFilename(label);
    try {
      const prepared = await Promise.all(chosen.map(shrinkQuestionImage));
      const result = multi
        ? await previewPracticeImport(prepared, target, label)
        : await previewPracticeImport(prepared[0], target);
      setPreview(result);
      setSelected(new Set((result.questions ?? result.samples).map((_, index) => index)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  async function commit() {
    if (!preview?.token || pending.current) return;
    if (rows.length > 0 && selected.size === 0) {
      setError(t("Select at least one question to import."));
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await commitPracticeImport(
        preview.token,
        rows.length > 0 ? [...selected].sort((a, b) => a - b) : undefined
      );
      onImported(t("Imported {{created}} questions; skipped {{duplicates}} duplicates.", result));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  return (
    <section
      aria-labelledby="practice-import-title"
      className="mb-6 rounded-2xl border border-border bg-card p-5 sm:p-6"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="practice-import-title" className="flex items-center gap-2 text-base font-semibold">
          <FileSpreadsheet size={18} />
          {t("Import questions")}
        </h2>
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label={t("Close import")}
          className="rounded-lg p-2 hover:bg-muted disabled:opacity-50"
        >
          <X size={17} />
        </button>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        {t(
          "Import one XLSX, CSV, TSV, JSON, PDF or DOCX file, or several question images as one group. Up to 500 questions per import. XLSX uses the active sheet and keeps row-anchored pictures; PDF/DOCX and images keep their figures. Chinese and English column names are supported.",
        )}
      </p>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          {t("Import into")}
          <select
            value={target}
            disabled={busy}
            onChange={event => {
              setTarget(event.target.value as typeof target);
              setPreview(null);
              setFilename("");
            }}
            className="rounded-lg border border-border bg-background p-2"
          >
            <option value="bank">{t("Question Bank")}</option>
            <option value="mistakes">{t("Mistakes")}</option>
          </select>
        </label>
        {(["xlsx", "csv"] as const).map(format => (
          <button
            key={format}
            type="button"
            onClick={() => {
              void downloadPracticeTemplate(format).catch(err => setError(String(err)));
            }}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs hover:bg-muted"
          >
            <Download size={14} />
            {t("Download {{format}} template", { format: format.toUpperCase() })}
          </button>
        ))}
      </div>
      <label className="mt-4 block rounded-xl border border-dashed border-border bg-muted/20 p-5 text-sm">
        <span className="mb-2 block font-medium">{t("Choose a question file")}</span>
        <input
          key={target}
          type="file"
          multiple
          accept=".xlsx,.csv,.tsv,.json,.pdf,.docx,image/png,image/jpeg,image/webp,image/gif"
          disabled={busy}
          onChange={event => {
            void select(event.target.files);
            event.target.value = "";
          }}
          className="block w-full text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-primary/10 file:px-3 file:py-2 file:text-primary"
        />
        <span className="mt-2 block text-xs text-muted-foreground">
          {t(
            "Select one spreadsheet, PDF or DOCX file, or several question images to import them as one group.",
          )}
        </span>
      </label>
      {busy && (
        <p role="status" className="mt-3 flex items-center gap-2 text-sm">
          <Loader2 size={16} className="animate-spin" />
          {t("Processing…")}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {error}
        </p>
      )}
      {preview && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="break-all text-sm font-medium">
              {filename} ·{" "}
              {t("{{valid}} of {{total}} rows are valid", {
                valid: preview.valid,
                total: preview.total,
              })}
            </p>
            {rows.length > 0 && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-muted-foreground">
                  {t("{{selected}} of {{total}} selected", {
                    selected: selected.size,
                    total: rows.length,
                  })}
                </span>
                <button
                  type="button"
                  onClick={toggleAll}
                  disabled={busy}
                  className="rounded-lg border border-border px-2 py-1 font-medium hover:bg-muted disabled:opacity-50"
                >
                  {allSelected ? t("Clear selection") : t("Select all")}
                </button>
              </div>
            )}
          </div>
          {preview.errors.length > 0 && (
            <div
              role="alert"
              className="max-h-40 overflow-auto rounded-lg bg-destructive/5 p-3 text-sm text-destructive"
            >
              <p className="mb-2 font-medium">
                {t("Nothing has been imported. Fix these rows and select the file again.")}
              </p>
              {preview.errors.map(error => (
                <p key={error.row}>
                  {t("Row {{row}}", { row: error.row })}: {error.message}
                </p>
              ))}
            </div>
          )}
          {solvedCount > 0 && (
            <p className="text-xs text-amber-700 dark:text-amber-300">
              {t("{{count}} answers were solved by the model — check them before importing.", {
                count: solvedCount,
              })}
            </p>
          )}
          <ol className="max-h-[26rem] divide-y divide-border overflow-auto rounded-lg border border-border px-3">
            {rows.map((question, index) => (
              <li key={index} className="flex gap-3 py-3 text-sm">
                {rows.length > 0 && (
                  <input
                    type="checkbox"
                    checked={selected.has(index)}
                    onChange={() => toggleRow(index)}
                    disabled={busy}
                    aria-label={t("Select question {{index}}", { index: index + 1 })}
                    className="mt-1 h-4 w-4 shrink-0"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-2">{question.question}</p>
                  <QuestionFigures images={question.question_images} className="mt-2" maxHeight="max-h-28" />
                  <p className="mt-1 truncate text-xs text-muted-foreground">
                    {t("Your answer")}: {question.user_answer || "—"} · {t("Correct answer")}:{" "}
                    {question.correct_answer || "—"}
                    {question.tags.length ? ` · ${question.tags.join(" / ")}` : ""}
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {question.attempt === "correct" && (
                      <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
                        {t("Answered correctly")}
                      </span>
                    )}
                    {question.answer_conflict && (
                      <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
                        {t("Check the answer")}
                      </span>
                    )}
                    {question.leaks?.length ? (
                      <span className="rounded-full bg-sky-500/10 px-2 py-0.5 text-[11px] text-sky-700 dark:text-sky-300">
                        {t("Blank restored")}
                      </span>
                    ) : null}
                    {!question.correct_answer && (
                      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
                        {t("No answer")}
                      </span>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ol>
          <p className="text-xs text-muted-foreground">
            {t(
              "Existing questions are kept. Identical imports are skipped and their tags are combined.",
            )}
          </p>
          <button
            type="button"
            disabled={busy || !preview.token || (rows.length > 0 && selected.size === 0)}
            onClick={() => void commit()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-40"
          >
            {rows.length > 0
              ? t("Import selected ({{count}})", { count: selected.size })
              : t("Confirm import")}
          </button>
        </div>
      )}
    </section>
  );
}
