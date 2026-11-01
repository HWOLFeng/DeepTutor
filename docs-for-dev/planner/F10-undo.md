# F10 · 整批撤回

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`e77f73f8`）|
| 里程碑 | M1 |
| 依赖 | F09 |
| 回滚 | `git revert <sha>`。回滚后 apply 仍可写入，但失去撤销能力——这是数据风险最高的回滚，回滚前先确认没有未撤回的批次 |

## 背景

任务板本身**没有任何 undo**，日历的 undo 只覆盖单次删除。一次「确认写入 6 条」如果写错了，用户只能手删。本功能给一批写入一个整批撤回：只撤这一批，且**跳过用户之后手工改过的条目**——撤销不能变成「帮你回滚你后来的修改」。

## 你的任务

只做一件事：在 F09 的 `deeptutor/api/routers/planner.py` 里新增撤回接口与实现。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/undo/{token}` | 整批撤回 |

行为规定：

1. 读 `plan_undo`：缺失 → `404`；`undone=1` → 幂等返回 `{"removed": 0, "skipped": [], "already_undone": true}`；
2. 逐条判定「是否被用户改过」——**实现改用了内容比对，而不是时间戳**：
   apply 时把每一行的期望值（`card_id` + 标题 + 日期、`event_id` + 起始时刻）写入 `plan_undo.after.items`，
   undo 时逐行比对。理由：卡片的 `updated_at` 是 `+00:00` 形式、草稿的 `applied_at` 是 `Z` 形式，
   字符串比较必错；而内容比对顺带覆盖了「改了又改回来」这种时间戳判不出来的情况；
3. 未被改过的：任务**硬删除**（不是归档——用户认知是「撤销」）；事件走 `CalendarStore.delete`；
   为此给 `TaskBoardStore` 补了 `delete()`（原 store 只有 create/update，没有删除）；
   删除会连带清掉 `session_tasks` 与 `legacy_imports` 里指向它的行，不留悬挂引用；
4. 被改过的：**跳过**，把 `title` 放进 `skipped`，不覆盖用户的新内容；
5. 标记 `mark_undone(token)`，草稿 `status="undone"`；
6. 返回 `{"removed": n, "skipped": [...], "already_undone": false, "revision": {...}}`；
6b. **合并来的卡永不删除**：apply 时 `merged` 的行记的是学习者自己的卡 id，撤销只撤本批**新建**的；
6c. 撤回后草稿状态为 `undone`，再次 apply → `409`；
7. 整个撤回在**一个** workspace 上下文里完成，且只影响快照里记录的 id。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F09-apply.md](F09-apply.md) | 快照结构与 `undo_token` 的产生位置 |
| [F01-draft-store.md](F01-draft-store.md) | `read_undo` / `mark_undone` |
| `deeptutor/services/calendar/store.py`（`restore`，约 390 行） | 既有的「快照改变则不覆盖」保护思路，本功能与它同构 |
| `deeptutor/services/task_board.py` | 卡片删除与 `updated_at` 字段 |
| `deeptutor/api/routers/calendar.py` | 错误封装与 workspace 头解析的写法，撤回接口要保持一致 |

## 硬性约束

- 只写这 3 个文件：`deeptutor/api/routers/planner.py`、`tests/api/test_planner_undo.py`、必要时 `deeptutor/api/router_index.json`（若新增了路径，必须脚本重建）；
- 不得改动 `apply` 的既有行为（只允许在 apply 里补快照字段，如果需要，明确写进交付说明）；
- 撤回**不得**删除本批之外的任何行；不得做全表清理；
- 撤回不产生聊天事件、不通知模型；
- 不加依赖、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pip install -q -r requirements/core.txt fastapi   # 若尚未安装
.venv/bin/python -m pytest tests/api/test_planner_undo.py -q
.venv/bin/python scripts/build_router_index.py && .venv/bin/python -m pytest tests/api/test_router_loader.py -q
```

测试必须覆盖：

1. apply 6 条 → undo → `cards` / `events` 恢复到 apply 前行数；
2. 手工改过其中 1 条任务的 title → undo 后该任务**仍在**，`skipped` 含它的标题；
3. 连调两次 undo → 第二次 `already_undone: true` 且 `removed == 0`；
4. 未知 token → `404`；
5. 草稿状态变为 `undone`，`GET /drafts/{id}` 能读到；
6. 应用了但**没有** undo_token 的历史数据（模拟）→ 撤回接口不报 500，返回 `404`/`409` 之一并有明确 detail。

实测结果：`tests/api/test_planner_undo.py` → `10 passed`；连 `tests/api/test_planner_router.py`
合计 `25 passed`；契约与路由索引三件套 `11 passed`；规划工具/服务 `117 passed`；
`tests/multi_user/test_task_board.py` + `test_calendar.py` → `37 passed`（store 新增 delete 未回归）；
`npm run contracts:check` 通过。

**文件清单偏离**：新增 `deeptutor/services/task_board.py`（补 `delete()`）。原稿写「任务硬删除」但 store
并没有删除方法；用归档替代会让每次撤销在账户数据里永久留下废行，判定为不可接受。

## 交付

- 产出物：`POST /api/planner/undo/{token}` + `TaskBoardStore.delete()` + 重导出的契约 + 测试
- 证据：pytest 全绿输出 + 索引校验输出 + 「跳过被改条目」用例说明
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
