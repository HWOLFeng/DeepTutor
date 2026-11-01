# F08 · plan_week 草稿生成

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`572d76c3`）|
| 里程碑 | M1 |
| 依赖 | F01、F05、F07 |
| 回滚 | `git revert <sha>`。回滚后模型不再能产出草稿；已写入的草稿行成为孤儿，不影响看板与日历 |

## 背景

前面几块都能读能写了，但「一次规划」还没人负责：把目标拆成任务 + 时间块，标出依赖、重复、冲突、强度，然后**停在草稿里等用户勾选**。这是整个产品最核心的一个功能，也是最容易被做成「模型自由发挥」的一个——所以规则必须写进代码，不写进提示词。

## 你的任务

只做一件事：新增草稿生成器 + `plan_week` 工具。**它绝不写 cards、绝不写 events**，只写 F01 的草稿表。

两部分：

**1. `deeptutor/services/planner/planner.py`**（纯逻辑，可单测）

```python
def build_plan(
    *, goal: str, week_start: date, workspace_id: str, tzid: str,
    candidates: list[PlanItemInput], existing_tasks: list[TaskCard],
    busy: list[Interval], policy: SlotPolicy,
) -> PlanProposal  # items: list[PlanItemInput]
```

**与原计划的偏离（实现时判定，均已写进代码注释）**

1. **返回 `PlanItemInput` 而不是 `PlanItem`**：生成器手里还没有 draf id，条目 id 由 store 按 payload
   顺序分配（`i1`…`iN`）。所以**先任务后时间块**、`depends_on` 直接写「store 将会分配的 id」，
   这是唯一能让依赖边对上号的顺序。
2. **工具多一个 `items` 参数**：没有候选就无从规划。模型只提议标题/日期/类型，估时、落位、
   去重、冲突、上限全部在代码里。
3. **估时按条目类型**（`task` 45 分钟、`review` 20 分钟），不是原稿里的 practice/reading 表——
   那需要模型上加一个 `type` 字段，属于后续细化，不在这里假装已经有了。
4. **每日上限约束的是「排进日历的时间块」**，不是任务卡数量：一天可以有 5 张卡，但只有 3 小时被排进日历。
   `locked` 的复习行永不因上限被丢弃。
5. **时间块落在到期日优先、否则最近的更早日期，绝不晚于到期日**——到期之后才排时间比不排更糟。
6. **会话 id 与语言由服务端注入**（`deeptutor/agents/loop/pipeline.py` 的 `plan_week` 分支），
   与 `cron` 同一条规矩：模型永远不指定草稿归属于哪个会话。为此 `pipeline.py` 加入本功能文件清单。
7. **草稿内同名同日条目直接丢弃**（没有可合并的对象），**看板已存在的同名同日条目保留并标
   `conflict="duplicate"`**（apply 时合并）。

职责与规则：

- **估时不由模型给**：按类型套固定区间常量（模块顶部集中定义，`review=5min/个`、`practice=(15,45)`、`reading=30min/章`），模型只能选 `kind`；
- **依赖边**：每个 `block` 的 `depends_on` 指向它服务的 `task` 的 `item_id`；没有可依附任务的时间块直接丢弃（**绝不允许孤立时间块**）；
- **重复检测**：与 `existing_tasks` 比对 `title + due_date`（归一化：去空白、小写、全角转半角）→ 命中者 `conflict="duplicate"`，不新建；
- **重叠检测**：`block` 与 `busy` 求交 → 命中者 `conflict="overlap"`，保留但标记；
- **强度**：当日/当周总时长超过 `policy.daily_max_min` 时，按 `kind` 优先级（review > practice > reading）截断，截掉的条目直接不产出；
- **SRS 条目**：`source="srs"` 且 `locked=True`（不可拒绝，只能改期，见 F11）；
- 输出顺序稳定（先 task 后 block，再按日期升序），`position` 连续。

**2. `PlanWeekTool`**（同一 `planner_tools.py`）

```
name: plan_week
输入：goal（必填）、week_start（YYYY-MM-DD，默认本周一）、preferences（可选：每天可用分钟）
输出：draft_id + 条目摘要 + 一句"等待用户确认"
```

工具内部：读任务板 + 读日历 + 读复习到期 → 调 `build_plan` → `get_plan_draft_store().create(...)` → 返回草稿摘要。**工具的 metadata 必须带 `{"plan_draft_id": ...}`**（F13 靠它渲染卡片）。

注册：`deeptutor/modules/manifests/calendar.json`（`order` 27）、`CONFIGURABLE_BUILTIN_TOOL_NAMES`、`deeptutor/agents/_shared/tool_composition.py` always-on。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F01-draft-store.md](F01-draft-store.md) | `create()` 的入参与条目字段（`depends_on` / `conflict` / `locked` 含义） |
| [F05-tasks-tool.md](F05-tasks-tool.md)、[F07-calendar-tool.md](F07-calendar-tool.md) | 读任务板与日历的正确姿势 |
| [F06-free-slots.md](F06-free-slots.md) | `SlotPolicy` 与 `Interval` |
| `deeptutor/services/task_board.py` | `TaskCard` 字段（title / due_date / status / archived） |
| `deeptutor/learning/scheduler.py` | 复习到期队列的取数方式（`build_review_queue`） |
| `deeptutor/services/calendar/occurrences` | `busy` 的来源 |

## 硬性约束

- 只写这 5 个文件：`deeptutor/services/planner/planner.py`、`deeptutor/tools/planner_tools.py`、`deeptutor/modules/manifests/calendar.json`、`deeptutor/tools/builtin/__init__.py`、`deeptutor/agents/_shared/tool_composition.py`，外加测试 `tests/services/planner/test_build_plan.py`、`tests/tools/test_planner_tools_plan_week.py`；
- **工具执行后 `cards` 与 `events` 行数必须与执行前完全相同**（这是本功能最重要的不变量）；
- 估时、强度、优先级全部是代码常量，不得让模型传 `duration_min` / `minutes`；
- 时间块必须依附任务，否则丢弃；
- 不加依赖、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/planner/test_build_plan.py tests/tools/test_planner_tools_plan_week.py -q
```

测试必须覆盖：

1. **零写入**：调完工具后 `cards` / `events` 表的行数与调用前相同（直接查 sqlite）；
2. 每个 `block` 都有 `depends_on`，且指向同草稿内存在的 task 条目；
3. 给一个重复标题 + 同日期的候选 → `conflict="duplicate"`，且不出现在待采纳条目里的「新任务」分组；
4. 给一个与既有事件重叠的时间块 → `conflict="overlap"`，仍保留；
5. 每日可用分钟很小时（例如 30）→ 当天条目总时长不超过上限；
6. 复习到期 N 个 → 产出且仅产出一条 `source="srs"`、`locked=True` 的条目；
7. 输出顺序与 `position` 稳定（同输入两次调用，条目顺序一致）。

实测结果：`tests/services/planner/test_build_plan.py` → `21 passed`；`tests/tools/test_planner_tools_plan_week.py`
→ `15 passed`；规划相关四个文件合计 `117 passed`；`tests/agents` → `511 passed, 13 skipped`（pipeline 改动未回归）；
`tests/modules` + 打包元数据 → `86 passed`；`tests/tools` 全量 `342 passed`（4 个既有失败与基线一致）。

**零写入不变量**：工具用例直接比对调用前后 `cards` 行数与 `events` occurrence 数，二者必须相等。

## 交付

- 产出物：`deeptutor/services/planner/planner.py`（`build_plan` / `PlanProposal` / `busy_from_occurrences`）+ `PlanWeekTool` + 三处注册 + pipeline 注入 + 两个测试文件
- 证据：pytest 全绿输出 + 「零写入」用例的断言说明
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
