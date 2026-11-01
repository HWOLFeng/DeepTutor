# F17 · 首页入口文案

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M3 |
| 依赖 | F11 |
| 回滚 | `git revert <sha>`。文案回滚，无数据影响 |

**环境前置**：Node 22 + `npm ci`（`i18n:check` 与前端测试都需要）。

## 背景

首页的 `plan` 分组已经存在，四条文案也已经在库里——但它们只要求「制定计划」，没说「排进任务和日历，先给我一份草稿确认」。结果就是模型写一段漂亮的计划文本，用户还得自己搬进看板。本功能**只改文案**，把入口接到 F08 的草稿路径上。

## 你的任务

只做一件事：改写 `starters.plan.*` 四种语言的引导文案（key 不变，组件不动），让它们落到「任务 + 日历 + 草稿确认」这条路径上。

规则：

1. key 保持 `starters.plan.{1..4}` 与 `starters.plan.{1..4}.prompt`，**不得新增 / 删除 key**（否则会与用户已保存的自定义结构错位）；
2. 每条 `prompt` 必须同时包含三件事：
   - 要排出**任务**和**日历时间块**（不能只产出文字计划）；
   - 不确定时先问（保留现有「最多问两个问题」的语气）；
   - 排完先给**草稿**，等用户勾选确认后再写入；
3. 语气保持现有风格：用户第一人称、口语、短句；
4. 6 个语言目录（`de`, `en`, `fr`, `pl`, `uk`, `zh`）同时改，语义一致、不逐字直译；
5. **不要**尝试覆盖用户已保存的自定义 starters（`chat.starters[group]` 优先是既有设计，保持不动）。

文案参考（zh，其余语言按语义重写）：

| key | 新文案 |
| --- | --- |
| `starters.plan.1.prompt` | 请帮我制定一份学习计划，并把结果排进我的任务和日历：拆成每周目标和每天能完成的小任务，标出复习节点。不确定的地方先问我，最多两个问题；排好之后先给我一份草稿让我勾选，我确认后再写入。 |
| `starters.plan.3.prompt` | 我快考试了，请帮我做一份冲刺复习方案并排进任务和日历：按重要程度排列要点，安排每天的复习内容与自测时间。如果我还没说科目、范围和剩余天数，请先问我；排完先让我确认草稿。 |

`starters.plan.2`（知识框架）与 `starters.plan.4`（薄弱环节）**不产出可写入的条目**，只需在末尾补一句「如果要落到任务和日历，告诉我，我会先给草稿」。

## 必读

| 文件 | 为什么 |
| --- | --- |
| `web/lib/page-customization.ts` | `starterPrompts()` 的取值优先级：用户自定义优先，key 结构不能动 |
| `web/locales/zh/app.json`（`starters.plan.*`，约 5462–5495 行） | 现有文案风格与 key 位置 |
| `web/components/chat/home/StarterSuggestions.tsx` | 只读：确认这些 prompt 的消费方式（选中即填入 composer） |
| [F08-plan-week.md](F08-plan-week.md) | 文案承诺的「草稿确认」正是这个工具的行为，不要承诺它做不到的事 |
| `web/scripts/i18n_parity.mjs` | 6 语言 key 必须齐全，缺一个就红 |

## 硬性约束

- 只写这 7 个文件：`web/locales/{de,en,fr,pl,uk,zh}/app.json`、`web/tests/plan-starter-copy.test.ts`；
- 不得修改组件、`web/lib/page-customization.ts`、不得新增 starter key；
- 不得改动其他分组（`explain` / `solve` / `practice` / `research`）的文案；
- 不提交。

## 验收

```bash
cd /root/DeepTutor/web
npm run i18n:check
npm run test:unit -- tests/plan-starter-copy.test.ts
```

测试必须覆盖：

1. 6 个语言的 `starters.plan.1.prompt` 与 `.3.prompt` 均非空且长度在合理区间（例如 60–400 字符），并且都含「草稿」语义的标记词（zh 用 `/草稿/`，en 用 `/draft/i`，其余语言各给自己的标记）；
2. 4 条 `starters.plan.*` 的 key 集合与改动前完全一致（用快照或显式列表比对）；
3. 每条 prompt 都提到「任务」与「日历」语义（zh: `/任务/`、`/日历/`；en: `/task/i`、`/calendar/i`）。

## 交付

- 产出物：6 个语言文件 + 1 个测试
- 证据：`i18n:check` 与测试输出（无 Node 则注明「未执行」）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
