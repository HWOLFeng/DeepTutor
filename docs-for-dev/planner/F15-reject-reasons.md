# F15 · 拒绝原因与会话去重

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M2 |
| 依赖 | F02、F13 |
| 回滚 | `git revert <sha>`。回滚后 `plan_items.reason` 列残留（无害，旧代码不读） |

## 背景

用户拒掉一条，如果下一轮又原样提出来，他会觉得这系统没在听他说话。拒绝原因既是**体验上的诚实**（说清为什么不要），也是**数据资产**（以后用来磨 skill 的偏好）。本功能做两件事：挡住「重复提案」，并给拒绝留一个可选的标签。

## 你的任务

只做一件事：给拒绝加可选原因 + 让同一会话内被拒的条目不再被重复提案。

**后端（两个文件）**

1. `deeptutor/services/planner/store.py`：`plan_items` 增加 `reason TEXT`（`NULL` 表示没填）。迁移用幂等 `ALTER TABLE`（先查 `PRAGMA table_info(plan_items)`，缺列才加），不写独立迁移脚本；`set_decisions` 增加可选 `reasons: dict[str, str] | None`，原因只能随 `dismissed` 写入，允许为空。
2. `deeptutor/services/planner/planner.py`：`build_plan` 增加参数 `dismissed_recent: list[str]`（已归一化的标题），命中者**直接不产出**；标题归一化规则与 F08 的重复检测共用一个函数（去空白、小写、全角转半角）。`PlanWeekTool` 负责查询：同一 `session_id`、最近 24 小时、`decision='dismissed'` 的条目标题。

**前端（一个文件）**

3. `web/features/planner/components/PlanDraftCard.tsx`：勾选框取消时，条目下方展开三个可选标签：`时间不合适` / `不是我想要的` / `已经有类似任务`；不选也能提交（**不得强制**）。标签随 `apply` 的请求体一起上送：`{"decisions": {...}, "reasons": {...}}`。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F08-plan-week.md](F08-plan-week.md) | 标题归一化与重复检测的既有实现，必须共用一个函数 |
| [F09-apply.md](F09-apply.md) | `apply` 的请求体形状要向后兼容（新增 `reasons` 不得破坏旧调用） |
| [F01-draft-store.md](F01-draft-store.md) | `set_decisions` 的现有约束（只允许 pending → accepted/dismissed） |
| [F13-chat-render.md](F13-chat-render.md) | 卡片渲染位置；本功能不改变位置 |
| `web/features/planner/components/PlanDraftCard.tsx` | 勾选状态模型 |

## 硬性约束

- 只写这 6 个文件：`deeptutor/services/planner/store.py`、`deeptutor/services/planner/planner.py`、`deeptutor/tools/planner_tools.py`、`deeptutor/api/routers/planner.py`（若 apply 需要透传 reasons）、`web/features/planner/components/PlanDraftCard.tsx`、`web/locales/*/app.json`，外加测试 `tests/services/planner/test_plan_dedupe.py` 与 `web/tests/plan-draft-reasons.spec.tsx`；
- 原因**可选**：不填不得报错、不得阻止提交；
- 去重只作用于**同一会话 + 24 小时内**的 rejected 条目；不得跨会话、不得永久记忆（那是以后个人化 skill 的事）；
- 不得把原因写进提示词或发给模型；
- 不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/services/planner/test_plan_dedupe.py -q
cd web && npm run test:unit -- tests/plan-draft-reasons.spec.tsx && npm run i18n:check
```

测试必须覆盖：

1. 上一轮 dismissed 了「第 3 章 特征值」，本轮同标题候选 → **不出现**在草稿里；
2. 换个会话 → 同一标题**会**出现；
3. 超过 24 小时 → 再次出现（用注入的「现在」验证，不 sleep）；
4. 归一化生效：`" 第3章  特征值 "` 与 `"第3章 特征值"` 视为同一条；
5. 后端：`reasons` 随 dismissed 落库，`accepted` 条目不允许带原因；
6. 前端：取消勾选 → 三个标签出现；不选原因也能提交；选了原因则 `apply` 请求体含 `reasons`；
7. 旧调用（不带 `reasons`）仍 200（向后兼容）。

## 交付

- 产出物：迁移 + 去重逻辑 + 原因 UI + 两侧测试
- 证据：两份测试输出
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
