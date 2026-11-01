# F06 · 空闲时段算法

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`19bfde07`）|
| 里程碑 | M1 |
| 依赖 | F00 |
| 回滚 | `git revert <sha>`。纯新增无副作用模块 |

## 背景

「这周哪天有空、能塞几个 45 分钟」这类计算不能交给模型——它一定会算错，而且每次错得不一样。先把算法做成**纯函数**：不碰数据库、不碰时区库以外的东西，用一组区间抠出可用的空档。

## 你的任务

只做一件事：新增 `deeptutor/services/planner/slots.py`，一个纯函数 + 数据类型 + 单测。

```python
class Interval(NamedTuple):
    start: datetime      # aware
    end: datetime        # aware

@dataclass(frozen=True)
class SlotPolicy:
    daily_max_min: int = 180     # 每天最多用于学习的分钟数（产品设定值，可被调用方覆盖）
    granularity_min: int = 15    # 最小时间块粒度
    day_start: str = "08:00"     # 学习时间窗（本地时间）
    day_end: str = "22:00"
    break_min: int = 10          # 两个连续块之间至少留白

def find_free_slots(
    busy: list[Interval],
    window: tuple[datetime, datetime],
    policy: SlotPolicy = SlotPolicy(),
    *,
    tzid: str,
) -> list[Interval]
```

规则：

1. 先按 `tzid` 把窗口切成「本地日 × 学习时间窗」，再逐日抠掉 `busy`；
2. 抠完的碎片向下取整到 `granularity_min`；小于 `granularity_min` 的碎片丢弃；
   **每个碎片作为一个完整窗口返回，不预先切成小段**（规划器要在一个窗口里放任务；把它切开只会逼调用方重新拼回去）。
   因 `break_min` 被裁短的窗口会**重新取整**，保证「每个窗口都是粒度的整数倍」这个不变量；
3. 同一天累计分配不超过 `daily_max_min`；
4. 相邻两个输出区间之间至少留 `break_min`（在分配时扣减，不是在输出时插入）；
5. `busy` 允许重叠、允许超窗、允许乱序——先归一化再计算；
6. 输出按时间升序、互不重叠、全部 aware 且在窗口内；
7. 无解返回 `[]`，不抛异常。

实现约束：时区与夏令时一律用 `deeptutor/services/calendar/recurrence.py` 的 `zone()`，不要 `datetime.now()`，不要 `time.localtime`。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定（时区只走 recurrence） |
| `deeptutor/services/calendar/recurrence.py` | `zone()` / `parse_instant` / `format_instant` 的语义 |
| `deeptutor/services/calendar/store.py`（`occurrences`） | `busy` 的最终来源，返回的是 aware datetime |
| `tests/services/test_calendar.py` | 本项目对 DST / 全日期的测试口味，照它写边界用例 |

## 硬性约束

- 只写这 3 个文件：`deeptutor/services/planner/slots.py`、`tests/services/planner/test_free_slots.py`、必要时 `tests/services/planner/__init__.py`；
- **纯函数**：不得读写数据库、不得导入 `task_board` / `calendar.store`；
- 不引入新依赖（`datetime` / `zoneinfo` / `dateutil` 已可用）；
- 不改任何既有文件；
- 不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/planner/test_free_slots.py -q
```

测试必须覆盖：

1. 空 `busy` → 返回**一个**整窗，长度 = `daily_max_min`（不是切成上限/粒度那么多块）；
2. `busy` 跨在窗口边界上 → 不产出越界区间；
3. 两个 `busy` 相邻 → 不让间隔小于 `break_min` 的碎块出现在结果里；
4. `busy` 相互重叠、乱序 → 结果仍升序、互不重叠（归一化生效）；
5. 全天塞满 → 返回 `[]`（不是异常）；
6. `daily_max_min` 很小（例如 30）→ 当天总分配不超过它；
7. 跨夏令时的一天（用 `Asia/Shanghai` 与一个 DST 时区各测一次）→ 不产生负长度或重复小时。

实测结果：`tests/services/planner/test_free_slots.py` → `17 passed`；连同类用例
`pytest tests/services/planner -q` → `41 passed`。

**与原计划的偏离**：原验收第 1 条要求「块数 = 上限/粒度」，实现时判定为错——规划器需要的是
「这段空档能放多久」，预先切成 15 分钟小段会让每个调用方再拼一次。已改为返回整窗，并写明理由。

## 交付

- 产出物：`deeptutor/services/planner/slots.py`（`Interval` / `SlotPolicy` / `find_free_slots`）+ 测试
- 证据：pytest 全绿输出（含用例数）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
