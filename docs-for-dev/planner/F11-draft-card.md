# F11 · 采纳卡片

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M2 |
| 依赖 | F04、F09 |
| 回滚 | `git revert <sha>`。回滚后规划仍可生成与写入，但只能通过 API 操作，聊天里没有可点的卡片 |

**环境前置**：需要 Node 22 + `web/` 内 `npm ci`。本机没有 Node，未装好前本功能只能做静态检查，**不得标 ☑**。

## 背景

草稿要变成「用户能勾选的东西」。这一屏是整个产品的成败点：勾得快、看得懂、敢点确认。缺了它，F08 生成的草稿对用户是不可见的。

## 你的任务

只做一件事：新增采纳卡片组件与它的 API 客户端，把「逐条勾选 → 提交采纳」跑通。不做聊天接入（F13）、不做卡内改期（F12）、不做撤回（F14）。

组件 `web/features/planner/components/PlanDraftCard.tsx`：

```
┌─ 本周学习计划（草稿 · 尚未写入）─────────────┐
│ 线代期中 · 预计 3.5h/周                      │
│ ▸ 新任务 3 条                                │
│   ☑ 第 3 章 特征值   90min · 11/02           │
│   ☐ 预习第 4 章      60min · 11/06           │
│ ▸ 时间块 2 个 · 依附任务                     │
│   ☑ 11/02 19:00–20:30 · 特征值               │
│ ▸ 复习 1 条 · 来自学习路径                   │
│   ☑ 到期复习 12 个   ~40min · 11/02  🔒      │
│─────────────────────────────────────────────│
│ 已选 3/6 · 3.5h    〔全不选〕〔取消〕〔确认写入 3 条〕│
└─────────────────────────────────────────────┘
```

交互规则（每条都是验收项）：

1. **分组**：`task` / `block` / `review` 三段，段内按日期升序；
2. **依赖联动**：取消某个 task，其 `depends_on` 指向它的所有 block 自动取消并置灰（不可单独勾回）；
3. **锁定项**：`locked === true`（SRS 复习）默认勾选，复选框禁用，只能通过 F12 改期；
4. **全选 / 全不选**：两个按钮同权重，都在底部主操作区，不藏进菜单；
5. **已选计数与时长**：`已选 N/M · 合计 Xh`，随勾选实时更新；
6. **强度提示**：合计时长超过草稿 `summary.weekly_limit_min` 时，底部出现黄色提示条（不阻止提交）；
7. **空选**：「确认写入」禁用，提示「至少选一条」；
8. **幂等**：提交中按钮 busy 且禁用，防双击；
9. **失败**：提交失败保留当前勾选，显示可重试的错误条。

API 客户端 `web/lib/planner-api.ts`（照 `web/lib/task-board-api.ts` 的风格）：`getDraft(id)`、`patchItem(draftId, itemId, changes)`、`applyDraft(draftId, decisions)`。

i18n：所有文案进 `web/locales/*/app.json`，key 前缀 `planner.`。**6 个语言目录都要加同一批 key**（`i18n:parity` 会拦）。

样式：新增 `web/features/planner/components/plan-draft.css`，用现有 CSS 变量（`--border` / `--accent` / `--muted-foreground`），不要引入 UI 库。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F09-apply.md](F09-apply.md) | 三个接口的请求 / 响应形状 |
| [F01-draft-store.md](F01-draft-store.md) | `kind` / `depends_on` / `locked` / `conflict` 的语义 |
| `web/features/calendar/components/EventEditor.tsx` | 本项目的表单与按钮惯例（间距、禁用态、错误条） |
| `web/lib/task-board-api.ts` | API 客户端的错误处理与响应解包写法 |
| `web/tests/task-links.spec.tsx` | 组件测试的写法（`Response.json` 打桩、无障碍查询） |
| `web/locales/en/app.json` | key 命名惯例 |

## 硬性约束

- 只写这 6 个文件：`web/features/planner/components/PlanDraftCard.tsx`、`web/features/planner/components/plan-draft.css`、`web/lib/planner-api.ts`、`web/tests/plan-draft-card.spec.tsx`、`web/locales/zh/app.json`、`web/locales/en/app.json`（其余 4 个语言文件只加同批 key）；
- 不得修改聊天消息渲染（F13）、不得实现改期（F12）、不得实现撤回（F14）；
- 不得引入新依赖（不加 UI 库、不加日期库，`date-fns` 已有）；
- 所有网络请求走 `web/lib/planner-api.ts`，组件内不直接 `fetch`；
- 不提交。

## 验收

```bash
cd /root/DeepTutor/web
npm run test:unit -- tests/plan-draft-card.spec.tsx
npm run typecheck
npm run i18n:check
```

测试必须覆盖：

1. 渲染出三段分组与各自条目数；
2. 取消 task → 其 block 变为未选且 `disabled`；
3. `locked` 条目：已勾选、`disabled`、点击无效果；
4. 计数与合计时长随勾选变化；全选后 `已选 M/M`；
5. 零选中 → 确认按钮 `disabled`；
6. 点击确认 → 只发出一次 `POST .../apply`，请求体 `decisions` 与勾选一致（连点两次也只一次）；
7. 提交失败（打桩 500）→ 勾选状态保留、错误条出现。

## 交付

- 产出物：组件 + 样式 + API 客户端 + 测试 + 6 语言文案
- 证据：三条命令的输出（若本机无 Node，写明「未执行」及原因）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
