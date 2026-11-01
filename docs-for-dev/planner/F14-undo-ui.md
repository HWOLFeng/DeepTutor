# F14 · 撤回入口

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`f27f3491`）|
| 里程碑 | M2 |
| 依赖 | F10、F13 |
| 回滚 | `git revert <sha>`。回滚后只能通过 API 撤回，界面上没有按钮 |

**环境前置**：Node 22.12+ 与 `web/` 内 `npm ci --legacy-peer-deps`（F11 已备好）。

## 背景

后端已经能整批撤回，但用户点不到它。撤回必须在**写入成功的那一刻**就在眼前——晚一步，用户就已经在日历里手删了。这是「可撤回」从能力变成体验的最后一步。

## 你的任务

只做一件事：卡片在 `applied` 之后显示写入结果与撤销按钮，点击后调用撤回接口并如实反馈。

规则：

1. `applied` 状态下的卡片顶部一行：`已写入 4 条 · 撤销`；
2. 点击「撤销」→ 二次确认（一行内联确认，不弹模态）→ `POST /api/planner/undo/{token}`；
3. 成功：该行变为 `已撤销 4 条`，勾选区隐藏，条目置灰并标「已删除」/「已修改，未撤回」；
4. **部分撤回**：接口返回 `skipped` 非空时，逐条列出「已修改，未撤回：<标题>」，不要笼统说「部分失败」；
5. 失败（404 / 409 / 500）：**确认条保持打开**、显示可读原因——用户本来就想撤回，重试应当是一次点击，
   而不是再走一遍同一个问题；
6. 已撤销的卡片刷新后仍是「已撤销」（状态来自 `GET /drafts/{id}` 的 `status`，不在本地记忆）；
7. 不使用 toast 作为唯一反馈——结果必须留在卡片上。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F10-undo.md](F10-undo.md) | 接口返回的 `removed` / `skipped` / `already_undone` 语义 |
| [F11-draft-card.md](F11-draft-card.md)、[F13-chat-render.md](F13-chat-render.md) | 卡片状态与渲染位置 |
| [F09-apply.md](F09-apply.md) | `apply_result` 里 `undo_token` 的来源与 `GET /drafts/{id}` 的可读性 |
| `web/features/calendar/components/CalendarPage.tsx`（第 470 行附近的 undo 提示条） | 本项目既有撤销交互的语言与样式 |

## 硬性约束

- 只写这 4 个文件：`web/features/planner/components/PlanDraftCard.tsx`、`web/lib/planner-api.ts`（加 `undoPlan`）、`web/tests/plan-draft-undo.spec.tsx`、`web/locales/*/app.json`（同批 key）；
- 不得修改后端；不得在本地缓存「已撤销」状态；
- 不得删除草稿记录（撤回是删条目，不是删草稿）；
- 不引依赖、不提交。

## 验收

```bash
cd /root/DeepTutor/web
npm run test:unit -- tests/plan-draft-undo.spec.tsx
npm run typecheck && npm run i18n:check
```

测试必须覆盖：

1. `applied` 卡片显示「已写入 4 条 · 撤销」；
2. 点击撤销 → 需要一次内联确认；确认后只发一次 `POST /undo/{token}`；
3. 成功（`removed: 4, skipped: []`）→ 行变为「已撤销 4 条」，勾选区消失；
4. `skipped: ["错题重做"]` → 出现「已修改，未撤回：错题重做」；
5. 接口 404 → 按钮仍在、显示原因；
6. `already_undone: true` → 直接呈现「已撤销」而非报错；
7. 刷新后（用 `status: "undone"` 的草稿桩）仍显示「已撤销」。

实测结果：

```bash
cd web
npx vitest run tests/plan-draft-undo.spec.tsx                       # 7 passed
npx vitest run tests/plan-draft-card.spec.tsx \
               tests/plan-draft-chat.spec.tsx \
               tests/plan-draft-undo.spec.tsx                      # 28 passed
npm run typecheck                                                   # exit 0
npm run i18n:parity                                                 # OK
```

覆盖的边界：确认条可取消（不发请求）、`skipped` 逐条列名、`already_undone` 当「已撤销」而非报错、
失败后确认条保持打开、刷新后从 `apply_result` 恢复「已写入 + 撤销」状态。

## 交付

- 产出物：`PlanDraftCard` 的「已写入 / 撤销 / 已撤销」三态 + `undoBatch()` + 测试 + 6 语言 6 个 key
- 证据：命令输出（无 Node 则注明「未执行」）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
