import test from "node:test";
import assert from "node:assert/strict";

import type { NotebookEntry } from "../lib/notebook-api";
import { MAX_VARIANT_SEEDS, buildQuizSeeds, variantSeedSelection } from "../lib/quiz-seeds";
import {
  DEFAULT_QUIZ_CONFIG,
  buildQuizWSConfig,
  summarizeQuizConfig,
  type QuizSeed,
} from "../lib/quiz-types";

function entry(overrides: Partial<NotebookEntry> = {}): NotebookEntry {
  return {
    id: 1,
    session_id: "",
    session_title: "",
    origin_type: "external_import",
    origin_ref: "practice-import",
    turn_id: "",
    question_id: "q1",
    question: "Solve 3x + 5 = 20.",
    question_type: "single_choice",
    options: { A: "5", B: "15", C: "8", D: "25" },
    correct_answer: "A",
    explanation: "3x = 15, so x = 5.",
    difficulty: "easy",
    user_answer: "",
    user_answer_images: [],
    question_images: [],
    source: "import",
    material_id: "import:tok",
    material_title: "lesson.csv",
    section_id: "",
    section_title: "",
    score_trend: "new",
    assessment_type: "quiz",
    result: "ungraded",
    mastery_path_id: "",
    knowledge_point_id: "",
    attempt_count: 1,
    hints_used: 0,
    confidence: null,
    response_time: null,
    quality: null,
    is_correct: false,
    resolved: false,
    bookmarked: false,
    followup_session_id: "",
    created_at: 1,
    updated_at: 1,
    categories: [{ id: 3, name: "一元一次方程", created_at: 1, entry_count: 1 }],
    ...overrides,
  } as NotebookEntry;
}

test("a picked question becomes a seed that keeps its shape", () => {
  const [seed] = buildQuizSeeds([entry()]);

  assert.equal(seed.question, "Solve 3x + 5 = 20.");
  // The bank's own type names map onto the composer's normalised set.
  assert.equal(seed.question_type, "choice");
  assert.deepEqual(seed.options, { A: "5", B: "15", C: "8", D: "25" });
  assert.equal(seed.correct_answer, "A");
  assert.equal(seed.explanation, "3x = 15, so x = 5.");
  assert.equal(seed.difficulty, "easy");
  // The tag names the set; the material is the next-best label.
  assert.equal(seed.topic, "一元一次方程");
  assert.equal(
    buildQuizSeeds([entry({ categories: [], material_title: "lesson.csv" })])[0].topic,
    "lesson.csv",
  );
});

test("bank question types keep their kind instead of collapsing to open text", () => {
  const types = buildQuizSeeds([
    entry({ id: 1, question_type: "single_choice" }),
    entry({ id: 2, question_type: "multi_choice", options: { A: "1", B: "2" } }),
    entry({ id: 3, question_type: "true_false", options: {} }),
    entry({ id: 4, question_type: "fill_blank", options: {} }),
    entry({ id: 5, question_type: "short_answer", options: {} }),
  ]).map((seed) => seed.question_type);

  assert.deepEqual(types, [
    "choice",
    "choice",
    "concept",
    "fill_in_blank",
    "short_answer",
  ]);
});

test("a selection is capped and empty questions are ignored", () => {
  const many = Array.from({ length: MAX_VARIANT_SEEDS + 3 }, (_, index) =>
    entry({ id: index + 1, question: `question ${index + 1}` }),
  );
  many.push(entry({ id: 99, question: "   " }));

  const selection = variantSeedSelection(many);

  assert.equal(selection.count, MAX_VARIANT_SEEDS + 3);
  assert.equal(selection.tooMany, true);
  assert.equal(selection.seeds.length, MAX_VARIANT_SEEDS);
  assert.deepEqual(
    selection.seeds.map((seed) => seed.question),
    ["question 1", "question 2", "question 3", "question 4", "question 5"],
  );
  assert.equal(variantSeedSelection([entry({ question: " " })]).tooMany, false);
});

test("seeds travel on the wire only when they exist", () => {
  const seed: QuizSeed = {
    question: "Solve 3x + 5 = 20.",
    question_type: "choice",
    options: { A: "5", B: "15" },
    correct_answer: "A",
    explanation: "…",
    difficulty: "easy",
    topic: "一元一次方程",
  };

  const topicConfig = buildQuizWSConfig({ ...DEFAULT_QUIZ_CONFIG, num_questions: 3 });
  assert.equal("seed_questions" in topicConfig, false);

  const seeded = buildQuizWSConfig({
    ...DEFAULT_QUIZ_CONFIG,
    num_questions: 2,
    seeds: [seed],
  });
  assert.equal(seeded.mode, "custom");
  assert.equal(seeded.num_questions, 2);
  assert.deepEqual(seeded.seed_questions, [
    {
      question: "Solve 3x + 5 = 20.",
      question_type: "choice",
      options: { A: "5", B: "15" },
      correct_answer: "A",
      explanation: "…",
      difficulty: "easy",
      topic: "一元一次方程",
    },
  ]);
});

test("the collapsed summary says the batch is seeded", () => {
  const summary = summarizeQuizConfig({
    ...DEFAULT_QUIZ_CONFIG,
    num_questions: 2,
    seeds: [
      { question: "Q1", question_type: "choice" },
      { question: "Q2", question_type: "choice" },
    ],
  });

  assert.ok(summary.includes("Variants"));
  assert.ok(summary.includes("2 seeds"));
  assert.ok(!summary.includes("Custom"));
});
