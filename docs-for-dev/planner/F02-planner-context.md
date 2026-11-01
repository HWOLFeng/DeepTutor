# F02 · 规划上下文聚合

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`049f638c`）|
| 里程碑 | M0 |
| 依赖 | F01 |
| 回滚 | `git revert <sha>`。纯新增只读模块，不影响现有对话 |

## 背景

模型现在对「今天几号、有哪些任务、哪些复习到期」一无所知——`session_tasks` 的关联从未进入上下文。没有这一块，后面所有工具都是在盲写。本功能只做**只读聚合 + 渲染成一段 markdown**，不注入提示词（注入是 F03），不调 LLM。

## 你的任务

只做一件事：新增 `deeptutor/services/planner/context.py`，把五类事实聚合成一个紧凑的 markdown 片段。

对外接口（签名照抄，后续功能依赖）：

```python
@dataclass
class PlannerSnapshot:
    today: str                 # "2026-11-01 Sun"
    tzid: str                  # "Asia/Shanghai"
    events: list[dict]         # {"at": "11/02 19:00", "title": "...", "task_id": str|None}
    tasks: list[dict]          # {"title": "...", "due": "11/04"|None, "id": "..."}
    linked_tasks: list[dict]   # 本会话已关联的任务，结构同上
    review_due: int            # 来自间隔重复引擎的到期数量
    degraded: list[str]        # 读取失败的数据源名

def load_planner_context(workspace_id: str, *, days: int = 7, tzid: str | None = None,
                         sources: PlannerSources | None = None,
                         session_id: str | None = None,
                         now: datetime | None = None) -> str

def render_planner_context(snapshot: PlannerSnapshot) -> str
```

渲染要求（模型可读、稳定、可裁剪）：

```
## 规划上下文
今天是 2026-11-01（Asia/Shanghai）。
- 未来 7 天已排事件 3 个：11/02 19:00 组会；11/04 20:00 线代复习
- 未完成任务 5 个：错题重做（11/04 到期）、预习第 4 章（无日期）…
- 本会话关联任务：错题重做
- 间隔重复到期 12 个
```

规则：
- 每个数据源单独 `try/except`，失败写进 `degraded` 并在文本末尾用一行 `（部分数据读取失败：events）`，**绝不抛异常**——规划上下文不能让整轮对话挂掉；
- 每次最多渲染 8 条事件 / 8 条任务 / 6 条关联任务（常量 `MAX_EVENT_ROWS` 等），超出的直接不渲染并标 `（已截断）`；
- 总量硬上限 1200 字符（`MAX_CONTEXT_CHARS`），仍超限时按「事件 → 任务 → 关联任务」顺序逐行丢弃，末尾写 `（已截断）`；标题超过 24 字符截断为 `…`；
- 全天事件渲染成 `MM/DD 全天`（它是本地日历日，**不得**做时区换算）；计时事件换算到用户时区后渲染 `MM/DD HH:MM`；
- 空数据渲染成 `暂无`，不省略标题行；
- 时间格式统一 `MM/DD HH:MM`，日期统一 `MM/DD`，全部通过 `recurrence.zone()` 换算，不手写时区逻辑。

数据来源（全部只读）：
- `get_calendar_store().occurrences(now, now+days, tzid)`
- `get_task_board_store().read()` → `workspace_board(board, workspace_id)` 过滤 `archived == False`
- `session_tasks`（本会话关联任务）经 `workspace_board(...).session_links`
- 复习到期：`deeptutor/learning/scheduler.py` 的 `SpacedRepetitionScheduler.build_review_queue`

这些 import **全部放在函数内部**（延迟导入），并通过 `PlannerSources` 可注入，测试用假数据。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定（时区、workspace 隔离） |
| `deeptutor/services/calendar/layers.py` | 已有一份「把学习/复习投影成日期块」的读法，可复用其查询方式 |
| `deeptutor/services/calendar/store.py` | `occurrences()` 的返回结构与时间参数 |
| `deeptutor/services/task_board.py` | `workspace_board()`、`session_links` 的结构 |
| `deeptutor/learning/scheduler.py` | 到期复习队列的取数方式 |
| `deeptutor/services/calendar/recurrence.py` | `parse_instant` / `zone` / `format_instant` |

## 硬性约束

- 只写这 3 个文件：`deeptutor/services/planner/context.py`、`tests/services/planner/test_planner_context.py`、必要时 `tests/services/planner/fakes.py`；
- **不得修改** `deeptutor/core/context.py`、`deeptutor/agents/loop/prompt_blocks.py`、`deeptutor/services/session/turns/executor.py`（那是 F03）；
- 不调 LLM、不发网络请求、不写任何库；
- `session_id` 只用于取「本会话关联任务」，缺省时该行渲染为 `暂无`；
- 延迟导入重型模块，保证只装 `pydantic` 的 venv 能跑测试；
- 不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/planner/test_planner_context.py -q
```

实测结果：`11 passed`（连同 F01 的 11 个用例，`pytest tests/services/planner -q` 共 `23 passed`）。

测试必须覆盖：

1. 有数据：渲染结果包含今天日期、时区、事件条数、未完成任务数、到期复习数五类信息；
2. 全空：渲染结果包含 `暂无`，且不抛异常；
3. 某一路数据源抛异常（例如 events 抛 `OSError`）：其余信息仍渲染，`degraded` 含 `events`，文本末尾含「部分数据读取失败」；
4. 超量截断：给 200 条事件，输出长度 `<= 1200` 且以 `（已截断）` 结尾；
5. 隔离：`workspace_id="a"` 的聚合结果不含 `workspace_id="b"` 的任务（用假 sources 断言过滤条件被传递）；
6. 真实数据源的事件映射：计时事件按用户时区换算、全天事件保持本地日历日（用打桩的 calendar store 断言 `11/02 19:00` 与 `11/04 全天`）；
7. `session_id` 被原样传给关联任务读取函数（`None` 与具体 id 各测一次）。

## 交付

- 产出物：`deeptutor/services/planner/context.py` + 测试
- 证据：pytest 全绿输出（含用例数）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
