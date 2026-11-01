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
| [F12](F12-inline-edit.md) | 卡内改期 | M2 | F11 | ☑ |
| [F13](F13-chat-render.md) | 聊天渲染接入 | M2 | F11 | ☑ |
| [F14](F14-undo-ui.md) | 撤回入口 | M2 | F10,F13 | ☑ |
| [F15](F15-reject-reasons.md) | 拒绝原因与会话去重 | M2 | F02,F13 | ☑ |
| [F16](F16-skill.md) | learning-plan skill | M3 | F08,F11 | ☑ |
| [F17](F17-entry-chips.md) | 首页入口 | M3 | F11 | ☑ |
| [F18](F18-daily-cron.md) | 每日计划自动化 | M3 | F13,F16 | ☑ |
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
- 前端功能（F11–F15、F17）→ **已在会话内装好**（Node 22.20 + `npm ci --legacy-peer-deps`，见 F11「环境实录」），
  因此这些功能的 vitest / typecheck 验收是真跑过的。若换到没有 Node 的机器，验收降级为静态检查并如实标「未执行」——
  **不得把未执行写成通过**。

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
| 2026-11-01 | F12 卡内改期 | `2c42402b` | 新增 `8 passed`；规划前端四套件 `36 passed`；`typecheck` exit 0 |
| 2026-11-01 | F16 learning-plan skill | `f10ad8cb` | 新增 `11 passed`；`tests/services/skill` → `66 passed` |
| 2026-11-01 | F17 首页入口文案 | `010e503b` | 新增 `5 passed`；6 语言 key 集合不变；`i18n:parity` OK |
| 2026-11-01 | F15 拒绝原因与会话去重 | `dff26a7a` | 新增后端 `14` + 前端 `6` 用例；契约重导出双端 `check` 通过 |
| 2026-11-01 | F18 每日计划自动化 | `a997de69` | 新增 `8 passed`（原记 `9 passed`，收尾复核时更正）；`tests/services/skill` → `74 passed`；端到端 7 行未执行（需真实实例） |
| 2026-11-01 | F04 裁撤 | — | 草稿 id 已随 `tool_result.metadata` 到达前端，新增事件类型会造成两个真相来源；F13 改为读 metadata |
| 2026-11-01 | 契约工具链打通 | `ce1a9139` | 用 `/tmp` 的临时 Node 22.11 完成 `openapi.json` + `generated/api.ts` 重生成，两端 `--check` 通过 |
| 2026-11-01 | 补跑漏掉的闸门 | `253e47f4` | 18 项验收都没跑 `npm run test:node`；补跑后抓到 2 条真实回归（原生 `title`、德语破折号），修复后 `1316 passed, 0 failed` |

---

# 收尾记录（2026-11-01）

规划已整体交付：**19 份子功能文档，18 项验收、1 项裁撤**。下文是留给后来者的账本——
哪些是照字面做的、哪些是判断后偏离的、哪些是前提被推翻的，以及哪些测试失败是**本来就红的**。

## 交付一览

| 功能 | commit | 功能 | commit |
| --- | --- | --- | --- |
| F00 验收环境 | 无（不改仓库） | F10 整批撤回 | `e77f73f8` |
| F01 草稿存储 | `f3140a7b` | F11 采纳卡片 | `88dcac1a` |
| F02 上下文聚合 | `049f638c` + `0de840cf` | F12 卡内改期 | `2c42402b` |
| F03 上下文注入 | `4f66ebc8` | F13 聊天渲染接入 | `f0ae1884` |
| F04 契约与事件 | **✗ 裁撤** | F14 撤回入口 | `f27f3491` |
| F05 tasks 工具 | `ef3eaa86` | F15 拒绝原因与去重 | `dff26a7a` |
| F06 空闲时段算法 | `19bfde07` | F16 learning-plan skill | `f10ad8cb` |
| F07 calendar 工具 | `0f9d4985` | F17 首页入口文案 | `010e503b` |
| F08 plan_week | `572d76c3` | F18 每日计划自动化 | `a997de69` |
| F09 采纳 API | `ce1a9139` | F19 读者时区 | `06e8c05b` |

## 三类决策

**照字面实现**（10 项）：F01、F03、F05、F07、F09、F11、F13、F17、F18、F19 的主体。

**判断后主动偏离**（每一条都在对应文档里写了理由）：

| 文档 | 偏离 | 为什么 |
| --- | --- | --- |
| F02 | 任务行只取「窗口内到期」切片 | 实测发现看板全量任务**早已**通过 `Task context` 进提示词，再列一遍是重复 |
| F06 | 返回整窗而不是切成粒度小块 | 规划器要在窗口里放任务；预先切开逼调用方重新拼 |
| F08 | 返回 `PlanItemInput`；工具多 `items` 参数；上限只约束时间块 | 生成器还没有 draft id；没有候选就无从规划；上限是「日历容量」不是「任务数」 |
| F10 | 用内容比对代替时间戳判断「改过没有」 | 卡片 `+00:00` 与草稿 `Z` 两种格式，字符串比较必错 |
| F10 | 给任务板补 `delete()` | 需要真删除，归档会在账户数据里永久留废行 |
| F13 | 新增 `PlanDraftPanel`；会话级「只激活最新」简化为按状态 | 60 行异步状态不该塞进 2500 行的消息列表；没有按会话列草稿的接口 |
| F15 | 原因用稳定 id；`PlanItem` 增字段；查询放 store | 不把中文当数据存；不返回就看不见；不把 SQL 写进工具 |
| F16/F18 | 各加一个测试文件 | 否则「skill 写对了」没有任何可执行判据 |

**前提被推翻**：**F04 整个裁撤**。计划要给草稿卡片新增 `plan_draft` 流事件，落地到 F08 时发现
草稿 id 已经随 `tool_result.metadata` 到达前端（与 `mastery_handoff` 同一套），新增事件会让同一事实
有两个真相来源。文档保留原文与裁撤理由，F13 改为读 metadata。

## 本来就红的测试（用 `git stash -u` 基线对比确认，不是本次欠的债）

| 套件 | 结果 | 原因 |
| --- | --- | --- |
| `tests/services/session` | 8 failed / 475 passed | `WorkspaceError: The selected workspace folder does not exist.` |
| `tests/api` | 62 failed / 1025 passed（基线 63/1009） | 多用户鉴权 `401`、缺可选模块依赖 |
| `tests/tools` | 4 failed / 342 passed | 缺 `arxiv`、`pptx_workspace_flow` |
| `npm run i18n:check` | exit 1 | de/pl 各 39 条既有硬编码文案（`features/settings/**`） |

`npm run typecheck`、`npm run contracts:check`、`npm run i18n:parity`、`pytest tests/services/planner`、
`pytest tests/services/skill`、`pytest tests/services/cron` 均**全绿**。

前端两条整套（收尾复核时全量跑过，不只是规划子集）：

| 命令 | 结果 |
| --- | --- |
| `npx vitest run` | 206 files / **1058 passed** |
| `npm run test:node` | **1316 passed, 0 failed** |

**教训（写在这里，因为它是本规划唯一的流程性失误）**：各功能文档的「验收」只列了与自己相关的
命令，`npm run test:node` 一次都没被列进去，于是仓库级规则（交互元素不许用原生 `title`、德语
词条不许用破折号）在两处被违反却没人看见——规矩本身都有测试，只是没人跑。`253e47f4` 修复，
并把这两条加进「怎么跑一遍」。

## 怎么跑一遍

```bash
# 后端（worktree 里已有 .venv）
.venv/bin/python -m pytest tests/services/planner tests/services/skill tests/api/test_planner_router.py -q

# 前端（需要 Node 22.12+，见 F11 的「环境实录」）
cd web && npx vitest run tests/plan-draft-*.spec.tsx tests/plan-starter-copy.spec.ts
npm run typecheck && npm run contracts:check

# 前端全量（改过共享组件或文案时必须跑：仓库级规则都在 test:node 里）
npm run test:node && npx vitest run
```

端到端（需要真实实例，见 [F18](F18-daily-cron.md) 的 7 行清单）尚未跑过——这是本规划唯一
**没有人点过一遍**的部分。

## 如果要继续

1. **个人化规划风格**：把 F15 收集的拒绝原因从「短期记忆」升级成 `my-planning-style` skill，
   让系统按用户自己的节奏排（这是 skill 机制在本产品里最不可替代的地方）；
2. **按会话列草稿的接口**（`GET /api/planner/sessions/{id}/drafts`）：F13 为此做了简化，
   有了它就能实现字面意义上的「只激活最新草稿」；
3. **周视图的计划入口**：目前只能从聊天发起，日历侧只有 automation 层能看见排程。
