# F18 · 每日计划自动化

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M3 |
| 依赖 | F13、F16 |
| 回滚 | `git revert <sha>`。已存在的 cron job 不会被回滚删除，需手工用 `cron` 工具的 `cancel` 清理 |

## 背景

「今天该干嘛」是使用频率最高的场景。它不该等用户想起来才发生——现有的 `cron` 工具已经能在会话里按时跑一轮对话，日历还会把它显示成 automation 条目。把这条通路接到规划上，只需一份 playbook 和一张端到端验收清单，不需要新代码。

## 你的任务

只做一件事：新增内置 skill `daily-plan`，并交付一份可照着走一遍的端到端验收清单。

`deeptutor/skills/builtin/daily-plan/SKILL.md`：

- frontmatter：
  ```yaml
  name: daily-plan
  description: Set up a recurring morning study-plan reminder that produces a plan draft for the day. Use when the user asks to be reminded each day, wants a standing "what should I do today" routine, or asks to automate their planning.
  tags: [规划]
  ```
- 正文规则：
  1. **怎么建**：用 `cron` 工具的 `schedule` 动作，`cron_expr` 形如 `"30 7 * * *"`，`tz` 取用户时区；`message` 必须是一句**自足的指令**（定时任务在新的一轮里跑，看不到当前对话），内容要明确「读今天的日历与到期复习，产出计划草稿给我确认，不要直接写入」；
  2. **不要重复建**：先 `action="list"` 看有没有同类 job，有就复用/取消旧的，不要每天多一个；
  3. **一次只建一个**：一句话里最多建一个 job；
  4. **明确告知副作用**：告诉用户这条 job 会出现在日历的 automation 层，以及怎么取消（`cancel` + job_id）；
  5. **失败不静默**：`cron` 返回 `ok=false` 时如实告诉用户原因，不要假装建好了。

`references/e2e.md`：端到端验收清单（见下）。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F16-skill.md](F16-skill.md) | 同类 skill 的写法与约束（不复述工具 schema） |
| `deeptutor/tools/cron_tool.py` | `schedule` / `list` / `cancel` 的实际参数与返回文案 |
| `deeptutor/services/cron/executor.py` | 到点时到底发生了什么：真在会话里跑一轮对话（`_reminder_prompt`） |
| `deeptutor/services/calendar/layers.py` | job 如何变成日历上的 automation 条目（含 workspace 过滤） |
| [F13-chat-render.md](F13-chat-render.md) | 到点那一轮的草稿卡片为什么会出现 |

## 硬性约束

- 只写这 2 个文件：`deeptutor/skills/builtin/daily-plan/SKILL.md`、`deeptutor/skills/builtin/daily-plan/references/e2e.md`；
- **不写代码**：本功能不应改动任何 `.py` / `.ts` / `.json`；如果实现过程中发现必须改代码，停下来在交付里记录缺口，而不是顺手改；
- 不加 `always: true`；
- 不提交。

## 验收

自动部分：

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/skill -q
.venv/bin/python -m pytest tests/services/cron -q
```

手工端到端（**必须逐条记录结果，不能只写"通过"**）：

| # | 步骤 | 期望观察 |
| --- | --- | --- |
| 1 | 在一个已关联工作区的会话里说「每天早上 7:30 帮我排今天的计划」 | 模型先 `cron list` 再建 job，并明确告知取消方式 |
| 2 | 再次说同一句 | 不产生第二个 job（模型复用或提示已存在） |
| 3 | 打开日历，看 automation 层 | 出现一条 07:30 的自动化条目，标题/消息可读 |
| 4 | 把 job 的 `at` 改成 2 分钟后（或直接等触发） | 该会话里出现一条新消息（cron 触发的一轮对话） |
| 5 | 该轮是否产出草稿卡片 | 出现 `PlanDraftCard`，且**日历与任务板尚未被写入** |
| 6 | 点「确认写入」 | 任务与时间块落库，卡片出现撤销入口 |
| 7 | 在日历里取消该自动化 | job 消失，次日不再触发 |

**环境前置**：`calendar` 模块必须启用（cron 归属该模块）；前端需要 Node 构建后的页面。

## 交付

- 产出物：2 个 skill 文件
- 证据：两份 pytest 输出 + 手工验收表格（7 行全部填写，未执行的行写「未执行」及原因）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
