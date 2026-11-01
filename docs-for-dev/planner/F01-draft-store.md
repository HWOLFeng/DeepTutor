# F01 · 草稿存储

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`f3140a7b`）|
| 里程碑 | M0 |
| 依赖 | F00 |
| 回滚 | `git revert <sha>`。本功能只新增表，代码回滚后残留表不被旧代码读取，无需迁移回删 |

## 背景

规划必须先「写成草稿」再让用户逐条勾选，而草稿要能跨刷新、跨标签页、跨会话恢复，还要支持「写入一次、撤回一次」。现在没有任何地方存这个中间态——`cards` 和 `events` 只能直接写终态。所以先做一个只做持久化的草稿存储，不接 HTTP、不接工具、不接前端。

## 你的任务

只做一件事：新增**草稿存储**与它的单元测试。三张表、一个 store 类、一个工厂函数，**不含任何 HTTP / 工具 / UI 代码，也不写 cards 或 events**。

数据文件：`get_account_path_service().get_runtime_state_dir() / "planner" / "drafts.sqlite"`（与 `task-board/tasks.sqlite`、`calendar/calendar.sqlite` 同级同风格）。

表结构（字段名照抄，后续功能依赖它们）：

```sql
plan_drafts(
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
  tzid TEXT NOT NULL, status TEXT NOT NULL,           -- drafting|reviewing|applied|undone|expired
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  calendar_revision INTEGER NOT NULL, board_revision INTEGER NOT NULL,
  summary_json TEXT NOT NULL, apply_result_json TEXT, undo_token TEXT, applied_at TEXT
);
plan_items(
  draft_id TEXT NOT NULL, item_id TEXT NOT NULL, kind TEXT NOT NULL,   -- task|block|review
  title TEXT NOT NULL, due_date TEXT, start_at TEXT, duration_min INTEGER,
  depends_on TEXT, conflict TEXT NOT NULL,          -- none|duplicate|overlap
  source TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0,
  decision TEXT NOT NULL DEFAULT 'pending',         -- pending|accepted|dismissed
  position INTEGER NOT NULL, PRIMARY KEY(draft_id, item_id)
);
plan_undo(
  token TEXT PRIMARY KEY, draft_id TEXT NOT NULL, created_at TEXT NOT NULL,
  before_json TEXT NOT NULL, after_json TEXT NOT NULL,
  task_ids_json TEXT NOT NULL, event_ids_json TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
```

store 必须提供并只提供这些方法：

- `create(...) -> PlanDraft`：写入草稿 + 条目，`expires_at = created_at + 24h`
- `read(draft_id) -> PlanDraft`：缺失抛 `KeyError`；过期按 `status` 计算为 `expired`，**仍可读**（重放要显示「已过期」）
- `set_decisions(draft_id, decisions: dict[str, str])`：只允许 `pending → accepted|dismissed`；`applied|undone|expired` 状态拒绝；未知 item_id 抛 `KeyError`
- `patch_item(draft_id, item_id, fields)`：只允许改 `title / due_date / start_at / duration_min`，**不得改 `decision` / `locked` / `depends_on`**
- `mark_applied(draft_id, result: dict, undo_token: str) -> tuple[PlanDraft, bool]`：返回 `(草稿, 是否首次)`；重复调用返回首次结果且 `False`
- `latest_open(session_id, workspace_id) -> PlanDraft | None`：同会话最新一个仍可复审的草稿（用于卡片「只激活最新一个」）
- `record_undo(token, draft_id, before: dict, after: dict, task_ids: list[str], event_ids: list[str])`
- `read_undo(token) -> PlanUndo`：缺失抛 `KeyError`；`mark_undone(token)`
- `purge_expired(now)`：删 `expires_at < now` 且 `status in ('drafting','reviewing','expired')` 的行

`create` 还要在落库前拒绝**孤立时间块**：`depends_on` 必须指向本次 payload 内的一个 `kind="task"` 条目，且不得自指。

工厂 `get_plan_draft_store()` 内部**延迟导入** `deeptutor.multi_user.paths`（照 `get_calendar_store` 的写法），保证测试可以只传路径。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定（草稿是唯一写入入口、幂等、撤回只撤本批） |
| `deeptutor/services/calendar/store.py` | 路径解析、`connect()` 事务风格、`revision` 与 `undo` 表的既有写法 |
| `deeptutor/services/task_board.py` | `workspace_id` 字段惯例、store 生命周期、`get_task_board_store` 的延迟导入写法 |
| `docs-for-dev/DATA_STORAGE.md` | 运行时数据落在哪个目录的约定 |

## 硬性约束

- 只写这 5 个文件：`deeptutor/services/planner/__init__.py`、`deeptutor/services/planner/models.py`、`deeptutor/services/planner/store.py`、`tests/services/planner/__init__.py`、`tests/services/planner/test_plan_draft_store.py`；
- 不得修改 `deeptutor/services/calendar/**`、`deeptutor/services/task_board.py`、任何 router / tool / 前端文件；
- 不引入新依赖：只用 `sqlite3`（标准库）与已装好的 `pydantic`；
- 不在 `deeptutor/services/planner/store.py` 顶层 import `multi_user`、`fastapi`、`workspaces`（保持模块可单独导入）；
  注意：`tests/conftest.py` 在导入期会拉起应用图，所以**跑测试仍需项目 core + server 依赖**（见 [F00](F00-acceptance-env.md)），「只装 pydantic」不够；
- 不写 HTTP、不注册路由、不加工具、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/planner/test_plan_draft_store.py -q
.venv/bin/python -m compileall -q deeptutor/services/planner
```

实测结果：`11 passed`，`compileall` 无输出。

测试必须覆盖且全部通过：

1. `create` 后可 `read`，条目顺序与 `position` 一致；
2. 未过期的草稿 `status == 'reviewing'`（`create` 后即可复审），过期后 `read` 返回 `expired` 而**不抛异常**；
3. 过期草稿 `set_decisions` 抛错；`applied` 草稿 `set_decisions` 抛错；
4. `set_decisions` 部分采纳后，未被提及的条目仍是 `pending`；
5. `patch_item` 能改 `due_date`，尝试改 `decision` 时报错；
6. `mark_applied` 连调两次：第二次返回同一 `apply_result` 且 `first_time is False`；
7. `record_undo` → `read_undo` → `mark_undone` 链路可用，`undone == 1`；
8. 未知 `draft_id` / `item_id` / `token` 一律抛 `KeyError`；
9. `depends_on` 指向草稿外条目、指向非 task、或自指 → `create` 抛 `ValueError`；
10. `latest_open` 在同一秒内连续创建两个草稿时仍返回**后建**的那个（`created_at` 只有秒精度，靠 `rowid` 兜底）。

## 交付

- 产出物：上述 5 个文件
- 证据：pytest 输出（用例数与全绿结果）、`compileall` 无输出
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
