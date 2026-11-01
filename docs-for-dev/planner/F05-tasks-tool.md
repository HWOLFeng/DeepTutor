# F05 · tasks 工具

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`ef3eaa86`）|
| 里程碑 | M1 |
| 依赖 | F01 |
| 回滚 | `git revert <sha>`。回滚后模型失去读写任务能力，看板数据不受影响 |

## 背景

任务看板现在只有前端能写。模型连「你有 5 个未完成任务」都是猜的，更不可能建卡、改期、标完成。本功能只给模型**任务**这一类的读写能力，日历留给 F07，规划留给 F08——先让最小的一件事真正可用。

## 你的任务

只做一件事：新增 `tasks` 工具并把它挂进 chat 的工具面。

工具定义（`deeptutor/tools/planner_tools.py`，新文件；`_PromptHintsMixin` 照 `deeptutor/tools/learning_journal_tools.py` 里那个本地实现，**不要**从 `deeptutor/tools/builtin/__init__.py` 导入以免循环依赖）：

```
name: tasks
actions:
  list       — 返回当前 workspace 未归档任务（title / due_date / status / id）
  create     — title 必填，note / due_date 可选
  update     — task_id 必填，可改 title / note / due_date
  complete   — task_id 必填，status 置 done
  reschedule — task_id 必填，due_date 必填（清空用 update + null）
```

硬性行为：

- **不接受 `workspace_id` 参数**：一律取 `current_workspace_id()`，跨 workspace 的任务视为不存在（返回「未找到」，不要泄露存在性）；
- 日期只接受 `YYYY-MM-DD`，非法值直接报错，不要猜；
- 所有 store 获取走模块级小函数（`_board_store()` / `_workspace()`），测试用 monkeypatch 替换，不依赖账户路径
  ——实测下来这是本功能唯一能让 19 个用例都不碰账户目录的办法；
- 每次执行返回 `ToolResult`：`content` 是人读摘要，`metadata` 带 `{"task_ids": [...]}`，便于后续功能/前端使用。

注册三处（缺一处工具不会生效）：

1. `deeptutor/modules/manifests/calendar.json`：`tools` 加 `"tasks": {"class_path": "deeptutor.tools.planner_tools:TasksTool", "order": 25}`，`paths` 加 `"deeptutor/tools/planner_tools.py"`；
2. `deeptutor/tools/builtin/__init__.py`：`CONFIGURABLE_BUILTIN_TOOL_NAMES` 加 `"tasks"`；
3. `deeptutor/agents/_shared/tool_composition.py`：always-on 元组加 `"tasks"`。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定（workspace 隔离、不提交） |
| `deeptutor/tools/learning_journal_tools.py` | 新工具模块的模板：本地 `_PromptHintsMixin`、延迟导入 store、`ToolResult` 风格 |
| `deeptutor/tools/mastery_nav.py` | 只读工具的元数据与卡片约定、id 校验先行的写法 |
| `deeptutor/services/task_board.py` | `CreateCard` / `UpdateCard` / `workspace_board` / store 方法，**必须复用它而不是自己写 SQL** |
| `deeptutor/modules/manifests/calendar.json` | 工具如何被模块清单声明 |
| `deeptutor/agents/_shared/tool_composition.py` | always-on 工具的挂载位置与 gating 规则 |

## 硬性约束

- 只写这 5 个文件：`deeptutor/tools/planner_tools.py`、`deeptutor/modules/manifests/calendar.json`、`deeptutor/tools/builtin/__init__.py`、`deeptutor/agents/_shared/tool_composition.py`、`tests/tools/test_planner_tools_tasks.py`；
- 不得直接执行 SQL，一律走 `TaskBoardStore` 的公开方法；
- 不得修改 router、前端、其他工具；
- 不改 `pyproject.toml`、不加依赖；
- 提示词 hints（`deeptutor/tools/prompting/hints/**`）本功能**不加**，留到文案定稿时统一补；
- 不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/tools/test_planner_tools_tasks.py -q
.venv/bin/python -c "from deeptutor.tools.builtin import CONFIGURABLE_BUILTIN_TOOL_NAMES as n; assert 'tasks' in n; print('registered')"
```

测试必须覆盖：

1. `list` 只返回当前 workspace、未归档的卡；
2. `create` 后能看到新卡，`due_date` 为空时不写脏值；
3. `create` 传非法日期 → 报错且不落库；
4. `update` / `complete` / `reschedule` 对**另一个 workspace 的 task_id** → 返回未找到，且数据库无变化；
5. 非法 `action` → 明确报错，不静默成功；
6. `update` 只改点名的字段（改期不动标题），`due_date=""` 能清空日期；
7. 注册三处齐全：`BUILTIN_TOOL_SPEC_BY_NAME` / `CONFIGURABLE_BUILTIN_TOOL_NAMES` / always-on 挂载元组
   （只注册两处时工具会静默不挂载，所以单独立一条用例）。

实测结果：`tests/tools/test_planner_tools_tasks.py` → `19 passed`；`tests/tools` 其余用例
`311 passed, 2 skipped`，其中 4 个失败（`knowledge_frontier` 缺 `arxiv`、`pptx_workspace_flow`）
经 `git stash -u` 基线对比确认为改动前既有失败；`tests/modules` + `tests/test_packaging_metadata.py`
→ `43 passed`（证明确实遵守了清单的路径归属与顺序规则）。

## 交付

- 产出物：`deeptutor/tools/planner_tools.py`（`TasksTool`）+ 三处注册 + 测试
- 证据：pytest 全绿输出 + `registered` 输出
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
