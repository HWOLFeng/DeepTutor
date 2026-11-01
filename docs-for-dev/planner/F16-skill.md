# F16 · learning-plan skill

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M3 |
| 依赖 | F08、F11 |
| 回滚 | `git revert <sha>`。回滚后模型失去流程知识，工具仍在（能力退化但不失效） |

## 背景

工具让模型**能**排计划，但「什么叫排得好」不在代码里：什么时候该问、拆多细、改期还是重排、拒绝后怎么接。这些是流程知识，正是 skill 的用途。本功能只写 playbook，不写代码逻辑——**所有确定性规则（估时、强度、依赖、重复检测）已经在 F06/F08 里，skill 不得重复定义它们**。

## 你的任务

只做一件事：新增内置 skill `deeptutor/skills/builtin/learning-plan/`。

`SKILL.md` 要求：

- frontmatter：
  ```yaml
  name: learning-plan
  description: Plan and repair a study schedule through chat — turn a goal into weekly tasks and calendar time blocks, triage overdue work, and answer "what should I do today". Use whenever the user asks to plan, reschedule, catch up, or asks what to study next.
  tags: [规划]
  ```
  **不要** `always: true`（规划不是每轮都需要，常驻会污染所有闲聊）。
- 正文（≤ 150 行）四段：
  1. **先判意图**：生成 / 每日收敛 / 滑期修复 / 只查询。只查询**绝不**调写工具。
  2. **生成流程**：不确定就先问（最多两个问题：目标、每天可投入时间）→ 调 `plan_week` → 用一段话解释取舍 → **停下等用户确认**。
  3. **滑期修复**：三选一（顺延 ≤2 天 / 降级为复习 / 明确放弃），禁止把全部逾期堆到今天。
  4. **硬规则（写成祈使句）**：不调 `apply`（它根本不是工具）；不编造任务 id / 日期 / 复习数量，一律来自工具返回；不逐条调 `create` 绕过草稿；不确定就问，不要猜。
- `references/limits.md`：估时与强度的产品设定值从**代码常量**说明（告诉模型「这些已经由 plan_week 决定，你不需要传，也不要口头许诺具体分钟数」），并列三个场景的对话片段。
- `references/examples.md`：三个完整对话范例（S1 从零生成 / S2 今天该干嘛 / S3 滑期修复），每个 ≤ 20 行。

## 必读

| 文件 | 为什么 |
| --- | --- |
| `deeptutor/skills/builtin/skill-creator/SKILL.md` | 本项目的 skill 编写规范：description 即触发器、正文 ≤500 行、渐进披露 |
| `deeptutor/services/skill/service.py`（文件头 docstring） | frontmatter 字段、`always` / `requires` 语义、只读层规则 |
| [F08-plan-week.md](F08-plan-week.md) | 工具名、参数、返回；**skill 里不要复述 schema** |
| [F11-draft-card.md](F11-draft-card.md) | 用户看到的是什么（解释取舍时要对齐这张卡的语义） |
| `deeptutor/skills/builtin/practice-template/SKILL.md` | 一个现有 skill 的篇幅与语气参照 |

## 硬性约束

- 只写这 3 个文件：`deeptutor/skills/builtin/learning-plan/SKILL.md`、`deeptutor/skills/builtin/learning-plan/references/limits.md`、`deeptutor/skills/builtin/learning-plan/references/examples.md`；
- 不得修改任何 Python / TS 代码；
- 不得在 skill 里定义估时数值、强度上限、依赖规则（它们已在代码里，重复写会造成两套事实）；
- 不得写「连续调 create」这类绕过草稿的流程；
- 不加 `always: true`；
- 不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/skill -q
.venv/bin/python - <<'PY'
from deeptutor.services.skill.service import SkillService
from pathlib import Path
svc = SkillService(Path("/tmp/skill-check"))
info = svc.get_detail("learning-plan")
print(info.name, "|", len(info.description), "chars")
assert "plan_week" in svc.read_skill_file("learning-plan")
assert "apply" in svc.read_skill_file("learning-plan")  # 以"禁止调用"形式出现
print("skill ok")
PY
```

测试必须覆盖 / 达标：

1. `tests/services/skill` 全绿（frontmatter 解析、名称规范、description 非空）；
2. 正文含 `plan_week`、含「不要调用 apply」的明确禁令、含三个场景；
3. 正文 ≤ 150 行、全文不含代码常量数值（用 `grep -E "180|15 ?min|30 ?min"` 自查，命中即需改写为引用代码）；
4. 名称全小写短横线、≤64 字符。

## 交付

- 产出物：3 个 skill 文件
- 证据：pytest 输出 + 脚本输出（`skill ok`）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
