"use client";

import type { NotebookAnswerImage } from "@/lib/notebook-api";

/** Figures that belong to the question itself (not the learner's answer). */
export function QuestionFigures({
  images,
  className = "",
  maxHeight = "max-h-44",
}: {
  images?: NotebookAnswerImage[] | null;
  className?: string;
  maxHeight?: string;
}) {
  const figures = (images ?? []).filter(image => image?.url);
  if (figures.length === 0) return null;
  return (
    <div className={`flex flex-wrap gap-2 ${className}`} data-testid="question-figures">
      {figures.map(image => (
        <a
          key={image.id || image.url}
          href={image.url}
          target="_blank"
          rel="noreferrer"
          className="block overflow-hidden rounded-lg border border-border bg-white"
        >
          <img
            src={image.url}
            alt={image.filename || "question figure"}
            loading="lazy"
            className={`${maxHeight} w-auto max-w-full object-contain`}
          />
        </a>
      ))}
    </div>
  );
}
