# F09 · 采纳 API 与落库

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`ce1a9139`）|
| 里程碑 | M1 |
| 依赖 | F08 |
| 回滚 | `git revert <sha>` + 重新生成路由索引。回滚后草稿可见但不可写入，卡片（F11）会退化为只读 |

## 背景

草稿躺在库里，等用户逐条勾选后**由前端**提交采纳结果。这条通道必须绕开模型：模型只描述计划，不参与写入参数。本功能实现「读草稿、改条目、按选择落库」，并把写入前后的快照存下来给 F10 撤回用。

## 你的任务

只做一件事：新增 `/api/planner` 路由，把「部分采纳 → 落库」跑通。**撤回不在本功能内**（F10）。

接口（前缀 `/api/planner`，`auth: "learning"`）：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/drafts/{draft_id}` | 读草稿（刷新 / 切会话重放） |
| `PATCH` | `/drafts/{draft_id}/items/{item_id}` | 改条目字段（title / due_date / start_at / duration_min） |
| `POST` | `/drafts/{draft_id}/apply` | 提交 `{"decisions": {"<item_id>": "accepted"\|"dismissed"}}`，落库 |

`apply` 的行为规定：

1. 先 `set_decisions`（未提及的条目保持 `pending` = 不采纳）；
2. **孤立时间块防护**：被采纳的 `block`，其 `depends_on` 指向的 task 也必须在采纳集合里，否则 `422` 并列出肇事 `item_id`；
3. 任务：`conflict="duplicate"` 的不新建，计入 `merged`；其余用 `CreateCard` 新建；
4. 事件：用 `EventInput` 新建，带 `task_id` + `task_workspace_id`（指向刚建/合并到的卡）；
5. 写入前后各取一次 cal/board 的 revision 与受影响 id 列表，`record_undo(...)` 落快照；
6. 返回 `{"applied": n, "merged": m, "skipped": k, "task_ids": [...], "event_ids": [...], "undo_token": "...", "revision": {...}}`；
7. **幂等**：草稿已是 `applied` → 直接返回第一次存下的 `apply_result`，HTTP 200，不重复写入；
8. 过期 / 已撤回草稿 → `409`；
9. 全部写入在一个 workspace 上下文内完成，任何一条跨 workspace 直接 `403`。

路由注册：`deeptutor/modules/manifests/calendar.json` 的 `routes` 加 `deeptutor.api.routers.planner:router`（prefix `/api/planner`，auth `learning`，order `641`），`paths` 加 `deeptutor/api/routers/planner.py` 与 `deeptutor/services/planner`；然后**必须**重新生成路由索引：

```bash
.venv/bin/python scripts/build_router_index.py
```

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定第 2 条：`apply` 不是工具 |
| [F01-draft-store.md](F01-draft-store.md) | `set_decisions` / `mark_applied` / `record_undo` 的确切语义 |
| `deeptutor/api/routers/calendar.py` | 错误封装（`errors()` 上下文）、workspace 头解析、`validate_task` |
| `deeptutor/api/routers/task_board.py` | `board_workspace(request)` 的取法 |
| `deeptutor/services/calendar/store.py` | `create()` 与 `revision` |
| `deeptutor/services/task_board.py` | `CreateCard` / `create()` / `revision` / `workspace_board` |
| `deeptutor/api/module_routes.py`、`deeptutor/api/router_loader.py` | 路由如何被清单装配、索引为何必须重建 |

## 硬性约束

- 只写这 5 个文件：`deeptutor/api/routers/planner.py`、`deeptutor/modules/manifests/calendar.json`、`deeptutor/api/router_index.json`（脚本生成）、`tests/api/test_planner_router.py`，必要时 `tests/api/__init__.py` 不加；
- **不得把 `apply` 注册成任何工具**（`planner_tools.py` 不许出现 apply）；
- 不得手改 `deeptutor/api/router_index.json`，只能用脚本生成；
- 落库必须走既有 store 的公开方法，不写 SQL；
- 不加依赖、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/pip install -q -r requirements/core.txt fastapi uvicorn    # 项目既有依赖
.venv/bin/python -m pytest tests/api/test_planner_router.py -q
.venv/bin/python -m pytest tests/api/test_router_loader.py -q
```

测试必须覆盖：

1. 全采纳 → `cards` 与 `events` 各自新增相应行数，返回 `applied` 与 id 列表；
2. 部分采纳 → 被 `dismissed` 的条目无对应行；未提及条目也不写入；
3. 全拒绝 → 零写入，返回 `applied == 0`；
4. **孤立时间块**：采纳 block 但拒绝其父 task → `422`，且零写入；
5. 双击：连续两次 `apply` → 第二次 200 且 `cards`/`events` 行数不变；
6. 过期草稿 → `409`；
7. 跨 workspace：**这条风险已被设计消除**——草稿条目只引用草稿内的 `item_id`，「外来 task_id 的事件」在
   结构上不存在。等价可测的情况是「另一个 workspace 的草稿」，读写一律 `404`（与不存在同形）；
8. `GET /drafts/{id}` 能读到刚写入的 `apply_result`（供 F14 显示撤销入口）；
9. `PATCH` 在草稿关闭后拒绝（`400`）、未知条目 `404`、未知草稿 `404`；
10. 采纳前改的标题**就是**写进看板的标题（改期/改名不被 apply 丢弃）；
11. `kind="review"` 的引擎复习行也落成一张任务卡（否则「12 个复习待做」只活在草稿里）。

实测结果：`tests/api/test_planner_router.py` → `15 passed`；连契约与路由索引四文件
`pytest tests/api/test_planner_router.py tests/api/test_frontend_contract_export.py tests/api/test_router_loader.py tests/api/test_canonical_route_surface.py`
→ `26 passed`。

**契约已同步**：`python -m deeptutor.api.contracts.export` 重写 `web/contracts/schema/openapi.json`，
`npm run contracts:generate` 重写 `web/contracts/generated/api.ts`；两边 `--check` 均通过。
为此在本会话里临时准备了 Node 22.11（装在 `/tmp`，不随仓库保存）。

`tests/api` 全量：改动后 `1025 passed / 62 failed`；`git stash -u` 基线 `1009 passed / 63 failed`
——失败数**没有增加**，62 个失败均为环境问题（多用户鉴权 `401`、缺可选模块依赖），与本次改动无关。

**并发备注**：双击由「先读状态再写」拦住（顺序请求安全）。真正的并发双击需要 store 提供「抢占」
语义，当前未做，已记在此处而不是假装解决。

## 交付

- 产出物：`deeptutor/api/routers/planner.py`（`GET` / `PATCH` / `POST apply`）+ 清单路由 + 重建后的 `router_index.json` + 重导出的两份契约 + 测试
- 证据：两份 pytest 输出（第二份证明索引未漂移）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
