# F07 · calendar 工具

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`0f9d4985`）|
| 里程碑 | M1 |
| 依赖 | F06 |
| 回滚 | `git revert <sha>`。回滚后模型失去日历读写能力；已存在的日程不受影响 |

## 背景

模型现在既看不到日历，也不能建时间块。而「把计划排进日历」这件事必须通过工具落地，否则 F08 的草稿会没有任何可执行的目标。本功能给模型日历的读 + 写 + 查空闲，但**不负责批量规划**（F08 才做，且只产草稿）。

## 你的任务

只做一件事：在 F05 建好的 `deeptutor/tools/planner_tools.py` 里新增 `CalendarTool`，并完成同样的三处注册。

```
name: calendar
actions:
  list_events      — start / end（ISO），返回该窗口内的 occurrence（含 task_id）
  create_event     — title / start / end / tzid / all_day? / task_id?
  update_event     — event_id + 要改的字段
  delete_event     — event_id（scope 固定 "all"，破坏性更大的一次一条）
  find_free_slots  — start / end，返回可用区间（内部调 F06 的纯函数）
```

硬性行为：

- 写入一律走 `get_calendar_store()` 的 `create` / `update` / `delete`，不碰 SQL；
- 事件的时间语义照 `EventInput`：`all_day` 用 `YYYY-MM-DD`（end 为开区间），计时事件用带偏移或 `Z` 的 instant，`tzid` 传用户时区；
- `task_id` 必须与当前 workspace 一致，否则拒绝——判定抽到 `deeptutor/services/planner/links.py`
  （`require_owned_task` / `owned_task_ids`），工具调用它；router 里的 `validate_task` 本功能**不动**（留给后续统一重构）。
  「别的空间的卡」与「不存在的卡」必须报**完全相同**的消息，否则工具会变成探测任务存在性的通道；
- `find_free_slots` 的 `busy` = 该窗口内事件 + 该窗口内时间块，不得把全天事件算成占用整窗（全天事件只占它那几天）；
- 只读操作失败返回 `success=False` + 人话原因，不要抛异常打断对话；
- `metadata` 带 `{"event_ids": [...]}` / `{"slots": [[start, end], ...]}`。

注册三处：`deeptutor/modules/manifests/calendar.json`（`"calendar"` 工具，注意与模块 id 同名不冲突，`order` 取 26）、`CONFIGURABLE_BUILTIN_TOOL_NAMES`、`deeptutor/agents/_shared/tool_composition.py` 的 always-on 元组。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F05-tasks-tool.md](F05-tasks-tool.md) | 同一个模块、同一套注册方式与测试替身约定 |
| [F06-free-slots.md](F06-free-slots.md) | `find_free_slots` 的入参与返回 |
| `deeptutor/services/calendar/models.py`（`EventInput`） | 时间与 `all_day` 的**唯一**正确语义 |
| `deeptutor/services/calendar/store.py` | `create` / `update` / `delete` / `occurrences` 的返回结构 |
| `deeptutor/api/routers/calendar.py`（`validate_task`） | task_id 与 workspace 的既有校验规则，必须共用 |
| `deeptutor/services/calendar/recurrence.py` | instant 解析与格式化 |

## 硬性约束

- 只写这 5 个文件：`deeptutor/tools/planner_tools.py`、`deeptutor/services/planner/links.py`（抽出的共用校验）、`deeptutor/modules/manifests/calendar.json`、`deeptutor/tools/builtin/__init__.py`、`tests/tools/test_planner_tools_calendar.py`；
- 若抽了 `deeptutor/services/planner/links.py`，`deeptutor/api/routers/calendar.py` **本功能不修改**（留给后续统一重构），工具自己调用新函数；
- 不得直接写 SQL、不得绕过 `EventInput` 校验；
- 不做批量创建（一次最多 1 条事件）；
- 不加依赖、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/tools/test_planner_tools_calendar.py -q
```

测试必须覆盖：

1. `create_event` 后 `list_events` 能看到它，`tzid` 与时间未被改写；
2. `all_day` 事件用日期串创建成功，`end` 为开区间（次日不加一天即失败）；
3. `task_id` 指向**另一个 workspace** 的任务 → 拒绝，且日历无新增；
4. `find_free_slots` 返回的区间与该窗口内的 `busy` 无交集；
5. 全天事件只占用其所在日期，不吞掉整窗（构造一个 3 天窗口断言）；
6. `delete_event` 未知 id → `success=False`，不抛异常；
7. `update_event` 只给 `start` 不给 `end` → 拒绝（时间成对改，不允许半个区间）；
8. 非法时区（`Mars/Olympus`）→ `success=False`，不静默偏移；
9. `links.require_owned_task`：跨 workspace / 不存在 / 已归档三种情况都拒绝，且前两者消息逐字相同。

实测结果：`tests/tools/test_planner_tools_calendar.py` → `16 passed`；`tests/services/planner/test_links.py`
→ `5 passed`；连同类 `pytest tests/services/planner tests/tools/test_planner_tools_calendar.py tests/tools/test_planner_tools_tasks.py`
→ `81 passed`；`tests/agents` → `511 passed, 13 skipped`；`tests/modules` + 打包元数据 → `86 passed`。

手工冒烟（临时 `CalendarStore`）：建 19:00–20:30 组会 → 空闲窗口变为 `11/02 08:00–11:00`；
在 11/03 建全天事件后，3 天窗口只保住 11/02 与 11/04 两天——**全天事件没有吞掉整窗**。

## 交付

- 产出物：`CalendarTool`（5 个 action）+ `deeptutor/services/planner/links.py` + 三处注册 + 两个测试文件
- 证据：pytest 全绿输出
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
