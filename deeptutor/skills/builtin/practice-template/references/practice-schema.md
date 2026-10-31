# Practice-template schema reference

The workbook is a flat question bank. The first row is always the header; every
later row is one question. Do not add columns, sheets, title rows, or totals.

## Columns in detail

### `question` (A)
The complete stem, in the source language. Remove the item number and the
book/section heading that precedes every question (e.g. `第3题 单元测评`).
Keep shared material (a passage, a figure caption, a table description) in the
stem of each question that depends on it.

- Good: `把 24 分米长的绳子对折 3 次，每段长（ ）分米。`
- Bad: `7. 把 24 分米…` (item number kept)
- Bad: `第4题 单元测评 把 24 分米…` (heading kept)

If the source uses `（ ）` blanks, keep them. Replace multiple spaces inside
blanks with a single full-width space (`（ ）`), not with the answer.

### `question_type` (B)
Exactly one of:

| Value | Use when | Answer shape |
| --- | --- | --- |
| `single_choice` | one correct option among A-D | `B` |
| `multi_choice` | two or more correct options | `A,C` |
| `true_false` | a statement judged true/false | `true` / `false` |
| `short_answer` | fill-in, calculation, proof, open response | text |

Do not use `single_choice` for a calculation that merely has numbers in it.
A question with no printed options is `short_answer` or `true_false`, not a
choice question.

### `A`-`D` (C-F)
The option *content* without the letter prefix or punctuation.

- Source `A. 140厘米` → `140厘米`
- Source `A、①②` → `①②`
- Leave a column blank when the option does not exist. Never write `无`,
  `N/A`, or `-`.

### `correct_answer` (G)
- Single choice: one uppercase letter, e.g. `B`.
- Multi choice: comma-joined uppercase letters in alphabetical order, e.g.
  `A,C` (not `AC`, not `A、C`).
- True/false: lowercase `true` or `false`.
- Short answer: the final answer text. For multiple blanks, use a readable
  ordered string such as `100厘米, 20分米` or `x=3, y=-2`; keep units.
- If the correct answer is genuinely unavailable, leave it empty and mention it
  in the final report. Never fill it with the student's marked answer.

### `explanation` (H)
A concise worked solution, one paragraph. Show the key step or reason, not the
whole textbook derivation. Keep the source language. Empty is acceptable for a
trivial item, but prefer a one-line reason.

### `tags` (I)
One to three short knowledge-point labels, comma-separated with **no spaces**:
`长度单位,减法`, `质数,数的认识`, `Reading,Inference`. Do not repeat the
question type or the grade level. Empty is allowed when the topic is unclear.

### `user_answer` (J)
The answer the student actually gave, when the source shows it (tick, circle,
handwriting, or a dedicated "我的答案" field). Blank when unknown. This column
is **not** the answer key.

In 作业帮-style mistake-notebook exports, a black ✓/√ printed over or beside an
option marks the student's selected (usually wrong) option. Record that letter
in `user_answer` and compute the real answer separately:

```
options: A ①②   B ②③   C ③④   D ①④
printed ✓ on D  ->  user_answer = D
actually correct -> correct_answer = A
```

## Source-pattern mapping

| Source marker | Maps to |
| --- | --- |
| `单选`, `单选题`, `下列…是` with A-D | `single_choice` |
| `多选`, `多选题`, `正确的是（多选）` | `multi_choice` |
| `判断`, `判断题`, `对的打√` | `true_false` |
| `填空`, `计算`, `解答`, `应用题` | `short_answer` |
| `【答案】`, `答案：`, `参考答案` | `correct_answer` |
| `【解析】`, `解析：`, `分析`, `详解` | `explanation` |
| `【知识点】`, `考点`, `标签` | `tags` |
| `我的答案`, tick/circle on an option | `user_answer` |

## Splitting rules

Split on the numbering that starts a new item: `第N题`, `N.`, `N、`, `N．`,
`（N）`. Do not split on numbers that are part of the content (decimals,
`2×2×2`, units, option text). A shared passage followed by sub-questions
`(1)(2)(3)` becomes one row per sub-question with the passage repeated in the
stem.

Tables in DOCX are flattened by joining cells with ` | `; if a table is the
question itself, reconstruct it as readable text rather than raw pipes.

## Worked examples

```json
[
  {
    "question": "2 + 2 = ?",
    "question_type": "single_choice",
    "A": "3", "B": "4", "C": "5", "D": "6",
    "correct_answer": "B",
    "explanation": "2 + 2 = 4.",
    "tags": "Math,Addition",
    "user_answer": ""
  },
  {
    "question": "Select the prime numbers",
    "question_type": "multi_choice",
    "A": "2", "B": "3", "C": "4", "D": "6",
    "correct_answer": "A,B",
    "explanation": "2 and 3 are prime.",
    "tags": "Math,Primes",
    "user_answer": "A,B"
  },
  {
    "question": "The Earth orbits the Sun",
    "question_type": "true_false",
    "A": "", "B": "", "C": "", "D": "",
    "correct_answer": "true",
    "explanation": "The Earth revolves around the Sun.",
    "tags": "Science",
    "user_answer": ""
  },
  {
    "question": "Explain photosynthesis",
    "question_type": "short_answer",
    "A": "", "B": "", "C": "", "D": "",
    "correct_answer": "Plants convert light energy into chemical energy.",
    "explanation": "Light + CO2 + H2O -> glucose + O2.",
    "tags": "Biology",
    "user_answer": ""
  }
]
```

## Final checks

1. Header A1:J1 is exactly `question, question_type, A, B, C, D,
   correct_answer, explanation, tags, user_answer`.
2. At least one row; no blank `question`.
3. Every `question_type` is one of the four allowed values.
4. Choice rows have at least two options and an answer letter that exists.
5. No answer-key value was copied from `user_answer` without solving.


## Figures in the workbook

Figures are not columns. The converter anchors each picture to column `K`, `L`,
`M`, or `N` on the question's row:

```
row 2: A..J = question 1;  K2 = figure 1
row 3: A..J = question 2;  K3 = figure 1, L3 = figure 2
```

The ten template columns stay untouched, and the practice importer reads the
picture's anchor row, so re-importing the workbook keeps the figures with the
right question. Keep a question below four figures; split an image-heavy
question or describe the extra pictures in the stem instead. Set
`FIGURES = "off"` when the user wants a text-only workbook.


### Figure verification

For scanned pages the first crop is only a proposal. The converter shows it
beside the full page and the question to the model, which may:

1. accept it (`complete: true`), or
2. return a larger `corrected_boxes` that contains the original crop, or
3. return a self-contained `redraw_svg` for a vector-like geometry diagram.

A replacement is used only after a second check accepts it; otherwise the
original crop stays. This trades a little surrounding whitespace for never
losing the top of a diagram or a dimension label. Set
`VERIFY_FIGURES = "off"` when the extra calls are not worth the cost, or when
the source is a clean raster scan whose boxes are already reliable.
