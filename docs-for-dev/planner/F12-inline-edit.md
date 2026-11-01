# F12 · 卡内改期

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`2c42402b`）|
| 里程碑 | M2 |
| 依赖 | F11 |
| 回滚 | `git revert <sha>`。回滚后只能整体采纳或拒绝，改期回到聊天里说 |

**环境前置**：Node 22.12+ 与 `web/` 内 `npm ci --legacy-peer-deps`（F11 已备好）。

## 背景

只给「采纳 / 拒绝」二选一时，用户真正想要的是「采纳但要改」——这类条目会被大量拒绝，采纳率会很难看。所以草稿里必须能就地改期、改估时，改完再采纳。

## 你的任务

只做一件事：在 `PlanDraftCard` 里加**条目内联编辑**（改 `due_date`、改 `start_at`/`duration_min`），提交走 `PATCH /drafts/{id}/items/{item_id}`。

规则：

1. 每个条目右侧一个「改」按钮，展开内联表单（不弹窗）；
2. `task` 只允许改日期；`block` 允许改开始时间 + 时长；`review` 只允许改日期（估时由引擎决定）；
3. **乐观更新**：先本地改，再发 `PATCH`；失败回滚到上一个值并提示；
4. 改期**不改变** `decision`（已勾选仍勾选、已拒绝仍拒绝）；
5. `locked` 条目**允许改期**，但不可取消勾选（与 F11 一致）；
6. 编辑中的条目在 `PATCH` 完成前不可提交采纳（避免提交旧值）；
7. 日期选择器用原生 `<input type="date">`，时间用 `<input type="time">`——不引第三方组件；
8. **改开始时间时保留服务端给的时区偏移**：从原 `start_at` 里取出 `+08:00`/`Z` 后缀再拼回去。
   用客户端本地时区重建 instant 会在跨时区时静默挪动时间——这是最容易错、也最难被发现的一行。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F11-draft-card.md](F11-draft-card.md) | 组件结构与既有交互，改期必须复用它的状态模型 |
| [F09-apply.md](F09-apply.md) | `PATCH` 允许改的字段（`title` / `due_date` / `start_at` / `duration_min`） |
| [F01-draft-store.md](F01-draft-store.md) | `patch_item` **不得改** `decision` / `locked` / `depends_on` |
| `web/features/calendar/components/EventEditor.tsx` | 日期与时间输入的既有写法（含时区处理） |
| `web/lib/planner-api.ts` | 已有的 `patchItem` 签名 |

## 硬性约束

- 只写这 4 个文件：`web/features/planner/components/PlanDraftCard.tsx`、`web/features/planner/components/plan-draft.css`、`web/tests/plan-draft-inline-edit.spec.tsx`、`web/locales/*/app.json`（同批 key）；
- 不得修改后端（`PATCH` 已在 F09 完成）；若发现接口缺字段，**停下来记录**而不是顺手改后端；
- 不得引入日期库；时区换算沿用项目现有工具；
- 不提交。

## 验收

```bash
cd /root/DeepTutor/web
npm run test:unit -- tests/plan-draft-inline-edit.spec.tsx
npm run typecheck && npm run i18n:check
```

测试必须覆盖：

1. 展开 `task` 条目改日期 → 发出一次 `PATCH`，payload 只含 `due_date`；
2. `PATCH` 返回 500 → UI 回滚到改前日期并出现错误提示；
3. 改期后勾选状态不变（原先勾选仍勾选，原先拒绝仍拒绝）；
4. `block` 改时长 → payload 只含 `duration_min`；
5. 编辑未保存时点「确认写入」→ 按钮禁用（或等待 PATCH 完成）；
6. `locked` 条目可改日期，复选框仍 `disabled`。

实测结果：

```bash
cd web
npx vitest run tests/plan-draft-inline-edit.spec.tsx                       # 8 passed
npx vitest run tests/plan-draft-{card,chat,undo,inline-edit}.spec.tsx     # 36 passed
npx vitest run tests/integration/chat-messages.spec.tsx                    # 13 passed（未回归）
npm run typecheck                                                          # exit 0
npm run i18n:parity                                                        # OK
```

覆盖的边界：只发改动的字段（`{due_date}` / `{duration_min}` / `{start_at}`）、失败回滚并报错、
改期不影响勾选、编辑未保存时禁止确认、`locked` 可改期但不可取消、取消编辑不发请求。

实现时发现并修掉的真 bug：偏移提取正则漏掉了秒位，导致改时间后拼成
`2026-11-03T19:30:00:00+08:00`（多一个 `:00`）——由「保留偏移」那条用例抓到。

## 交付

- 产出物：`PlanDraftCard` 的行内编辑器（date / time + duration）+ 样式 + 测试 + 6 语言 6 个 key
- 证据：命令输出（无 Node 则注明「未执行」）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
