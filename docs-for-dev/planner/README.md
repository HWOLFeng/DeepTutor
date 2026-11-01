# 学习规划：聊天 × 任务 × 日历 — 子功能实施计划

把产品规划拆成 18 个**可独立实现、独立验收、独立回滚**的子功能。
**一个功能一份文档，一份文档一次提交。**

---

## 使用流程

1. 取状态为 `☐ 未开始` 且依赖全部 `☑ 已验收` 的文档（顺序见「状态总览」）。
2. 严格按该文档的「你的任务」与「硬性约束」实现，**不要顺手做下一个功能**。
3. 跑「验收」里的命令，把关键输出抄进本文档末尾的「变更记录」。
4. 全绿 → 把该文档状态改为 `☑ 已验收` 并写上 commit sha → 提交（一个功能一个 commit）。
5. 出问题 → `git revert <sha>`，状态改回 `☐`，在「变更记录」写一行原因。

状态标记：`☐ 未开始` / `◐ 进行中` / `☑ 已验收` / `✗ 已回滚` / `✗ 裁撤`（计划本身被判定为不必要或有害，
文件保留判断理由，不进入实现）

---

## 全局设计约定（所有文档共享，冲突时以本节为准）

1. **草稿是唯一写入入口**：`plan_week` 只产出草稿，绝不直接写 cards / events。
2. **`apply` 不是工具**：模型没有权限调用，只有前端可达的 HTTP 接口。模型永远不接触写入参数。
3. **workspace 隔离**：任何跨 workspace 的读取或写入必须拒绝；每次写入前解析当前 workspace。
4. **时区**：一律走 `deeptutor/services/calendar/recurrence.py` 的 `parse_instant` / `zone`，不自己换算。
5. **幂等**：`apply` 双击只生效一次，第二次返回首次结果。
6. **撤回只撤本批**：apply 之后被用户手工改过的条目跳过并提示，不做覆盖式恢复。
7. **不加新依赖，不改构建配置。**
8. **实现阶段不提交**（提交是验收环节的动作）。
9. 每个功能的代码范围 = 该文档「硬性约束」里列出的文件清单，**清单外的文件一律不动**。

---

## 状态总览

| 文档 | 功能 | 里程碑 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| [F00](F00-acceptance-env.md) | 验收环境 | M0 | — | ☑ |
| [F01](F01-draft-store.md) | 草稿存储 | M0 | F00 | ☑ |
| [F02](F02-planner-context.md) | 规划上下文聚合 | M0 | F01 | ☑ |
| [F03](F03-context-injection.md) | 上下文注入 | M0 | F02 | ☑ |
| [F04](F04-contracts.md) | 契约与 plan_draft 事件 | M0 | F00 | ✗ 裁撤 |
| [F05](F05-tasks-tool.md) | tasks 工具 | M1 | F01 | ☑ |
| [F06](F06-free-slots.md) | 空闲时段算法 | M1 | F00 | ☑ |
| [F07](F07-calendar-tool.md) | calendar 工具 | M1 | F06 | ☑ |
| [F08](F08-plan-week.md) | plan_week 草稿生成 | M1 | F01,F05,F07 | ☑ |
| [F09](F09-apply.md) | 采纳 API 与落库 | M1 | F08 | ☑ |
| [F10](F10-undo.md) | 整批撤回 | M1 | F09 | ☑ |
| [F11](F11-draft-card.md) | 采纳卡片 | M2 | F09 | ☑ |
| [F12](F12-inline-edit.md) | 卡内改期 | M2 | F11 | ☐ |
| [F13](F13-chat-render.md) | 聊天渲染接入 | M2 | F11 | ☑ |
| [F14](F14-undo-ui.md) | 撤回入口 | M2 | F10,F13 | ☑ |
| [F15](F15-reject-reasons.md) | 拒绝原因与会话去重 | M2 | F02,F13 | ☐ |
| [F16](F16-skill.md) | learning-plan skill | M3 | F08,F11 | ☐ |
| [F17](F17-entry-chips.md) | 首页入口 | M3 | F11 | ☐ |
| [F18](F18-daily-cron.md) | 每日计划自动化 | M3 | F13,F16 | ☐ |
| [F19](F19-turn-timezone.md) | 规划上下文的读者时区 | M3 | F03 | ☑ |

**关键路径**：F00 → F01 → F08 → F09 → F11 → F13 → F14。

F19 是 F03 交付时暴露的补漏项（「今天」的时区还取自宿主机），不阻塞关键路径，但要排在给人试用之前。

里程碑：M0 地基（F00–F04）、M1 工具与服务端（F05–F10）、M2 前端闭环（F11–F15）、M3 沉淀与入口（F16–F18）。
估算（单人，含测试）：M0 约 5 人日、M1 约 11 人日、M2 约 8 人日、M3 约 2.5 人日。

---

## 验收环境现状（本机实测，勿假设）

| 事实 | 证据 | 影响 |
| --- | --- | --- |
| 系统 python3 无项目依赖 | `python3 -c "import fastapi"` → `ModuleNotFoundError` | 后端测试必须用 F00 建的 `.venv` |
| 系统无 pip / ensurepip | `pip3: command not found`、`No module named ensurepip` | 已通过 get-pip 引导进 `.venv`（F00） |
| Node 需 22.12+ | Node 22.11 下 `jsdom` 报 `ERR_REQUIRE_ESM`（`require(esm)` 未默认开启）| 用 **22.20**；`web/node_modules` 用 `npm ci --legacy-peer-deps` 装（约 1.3GB），并手工补 `@rolldown/binding-linux-x64-gnu` 的 tarball——`npm install` 补装会重解整棵树，实测会把 `jsdom` 弄坏 |

因此：
- 后端功能 → 用 `.venv/bin/python -m pytest ...` 验收，可执行。
- 前端功能（F04 的后半、F11–F15、F17）→ 在装好 Node 22 + `web/` 内 `npm ci` 之前，验收降级为：文件静态检查 + 明确标注「未执行」。**不得把未执行写成通过。**

---

## 提交与回滚规程

- 分支：直接在当前分支，一次一个 commit。
- message：`feat(planner): F05 tasks 工具`、`fix(planner): F09 apply 幂等`、`revert: F05 tasks 工具`。
- 回滚：`git revert <sha>`。
- 若该功能**建了表**（F01），回滚代码不会删表：残留表不影响旧代码（旧代码不读它），在下一次 clean 时再处理，不要写迁移脚本回删。
- 若该功能**改了两个枚举**（F04），必须同时 revert 两处，否则前端解析会拒绝未知事件类型。

---

## 变更记录

| 日期 | 功能 | commit | 验收结论 |
| --- | --- | --- | --- |
| 2026-11-01 | 文档集建立（F00–F18 + README） | — | 文档完成 |
| 2026-11-01 | F00 验收环境 | 无（不改仓库） | `pytest 9.2.0` / `pip 26.2.2` / `deps ok`；前端仍无 Node |
| 2026-11-01 | F01 草稿存储 | `f3140a7b` | `pytest tests/services/planner -q` → `11 passed`；`compileall` 无输出 |
| 2026-11-01 | F02 规划上下文聚合 | `049f638c` + `0de840cf` | `pytest tests/services/planner -q` → `24 passed`；典型日渲染 188 字符（上限 1200）|
| 2026-11-01 | F03 上下文注入 | `4f66ebc8` | 新增 `4 passed`；`pytest tests/agents` → `500 passed, 13 skipped` |
| 2026-11-01 | F19 读者时区 | `06e8c05b` | 新增 `11 passed`；`pytest tests/agents` → `511 passed, 13 skipped`；前端改动未验证（无 Node）|
| 2026-11-01 | F05 tasks 工具 | `ef3eaa86` | 新增 `19 passed`；`tests/tools` → `311 passed`（4 个既有失败已验证）；`tests/modules` → `43 passed` |
| 2026-11-01 | F06 空闲时段算法 | `19bfde07` | 新增 `17 passed`；`tests/services/planner` → `41 passed` |
| 2026-11-01 | F07 calendar 工具 | `0f9d4985` | 新增 `21 passed`（含 links）；`tests/agents` → `511 passed`；`tests/modules` → `86 passed` |
| 2026-11-01 | F08 plan_week | `572d76c3` | 新增 `36 passed`；零写入不变量已验证；`tests/agents` → `511 passed` |
| 2026-11-01 | F09 采纳 API | `ce1a9139` | 新增 `15 passed`；契约双端 `--check` 通过；`tests/api` 失败数未增加（62 vs 基线 63）|
| 2026-11-01 | F10 整批撤回 | `e77f73f8` | 新增 `10 passed`；`tests/multi_user` 任务板/日历 `37 passed`；契约 `check` 通过 |
| 2026-11-01 | F11 采纳卡片 | `88dcac1a` | vitest `11 passed`；`typecheck` exit 0；`i18n:parity` OK（audit 失败经基线确认为既有） |
| 2026-11-01 | F13 聊天渲染接入 | `f0ae1884` | 新增 `10 passed`；消息列表集成测试 `27 passed`；`typecheck` exit 0 |
| 2026-11-01 | F14 撤回入口 | `f27f3491` | 新增 `7 passed`；规划前端三套件 `28 passed`；`typecheck` exit 0 |
| 2026-11-01 | F04 裁撤 | — | 草稿 id 已随 `tool_result.metadata` 到达前端，新增事件类型会造成两个真相来源；F13 改为读 metadata |
| 2026-11-01 | 契约工具链打通 | `ce1a9139` | 用 `/tmp` 的临时 Node 22.11 完成 `openapi.json` + `generated/api.ts` 重生成，两端 `--check` 通过 |
