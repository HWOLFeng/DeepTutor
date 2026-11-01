# F04 · 契约与 plan_draft 事件

| 项 | 值 |
| --- | --- |
| 状态 | ✗ 已裁撤（2026-11-01，见文末「裁撤理由」）|
| 里程碑 | M0 |
| 依赖 | F00 |
| 回滚 | `git revert <sha>`。**必须连同两处枚举一起 revert**，否则前端解析会拒绝未知事件类型 |

## 背景

> **本文件已裁撤，不要按它实现。** 保留原文是为了留下判断痕迹：计划里「新增 `plan_draft` 事件类型」这一条，
> 在 F08 落地后发现是多余的——草稿 id 已经随 `tool_result` 事件的 `metadata` 出来了。

## 原计划（保留，不再执行）

以下为裁撤前的原文，作为判断依据留档。

采纳卡片要能在聊天流里出现并且刷新后可重放，就必须是一个**正式的事件类型**，而不是塞进 `metadata` 的临时约定。项目的线协议是「Python 模型 → JSON Schema → TS 类型」单向生成的，所以新增事件类型要从枚举一路改到生成物，缺一环 `contracts:check` 就会红。

## 你的任务

只做一件事：新增 `plan_draft` 事件类型并让契约链路自洽。**不做任何业务逻辑、不渲染卡片**（那是 F11/F13）。

1. `deeptutor/core/stream.py` 的 `StreamEventType`：在 `WAIT_FOR_INPUT = "wait_for_input"` 之后加 `PLAN_DRAFT = "plan_draft"`。
2. `deeptutor/api/contracts/turn_protocol.py` 的同名枚举：加同一条（两份枚举必须逐字一致，这是既有约定）。
3. 重新导出契约：
   ```bash
   .venv/bin/python -m deeptutor.api.contracts.export
   ```
   它会重写 `web/contracts/schema/openapi.json` 与 `web/contracts/schema/turn-protocol.json`。
4. 前端在 `web/contracts/schema` 更新后重新生成 TS：
   ```bash
   cd web && npm run contracts:generate
   ```
5. 前端两处事件白名单要接纳新类型：`web/contracts/parse/turn-event.ts`、`web/features/chat/transport/UnifiedTurnClient.ts`。
6. 前端两个断言枚举清单的测试同步：`web/tests/generated-contracts.test.ts`、`web/tests/turn-protocol-parser.test.ts`。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定（`apply` 不是工具、不提交） |
| `deeptutor/core/stream.py` | 运行时事件枚举，全项目事件类型的源头之一 |
| `deeptutor/api/contracts/turn_protocol.py` | 线协议枚举，与上一处必须保持一致 |
| `deeptutor/api/contracts/export.py` | `render_contracts()` / `write_contracts()` 的生成入口 |
| `web/scripts/generate-contracts.mjs` | JSON Schema → TS 的生成方式（读它才能判断必须重跑什么） |
| `web/contracts/parse/turn-event.ts`、`web/features/chat/transport/UnifiedTurnClient.ts` | 事件白名单，不加会被直接丢弃 |
| `tests/api/test_frontend_contract_export.py` | 既有契约漂移测试，照它的断言风格加一条 |

## 硬性约束

- 只写这 8 个文件：`deeptutor/core/stream.py`、`deeptutor/api/contracts/turn_protocol.py`、`web/contracts/schema/openapi.json`、`web/contracts/schema/turn-protocol.json`、`web/contracts/generated/turn-protocol.ts`、`web/contracts/parse/turn-event.ts`、`web/features/chat/transport/UnifiedTurnClient.ts`、`tests/api/test_frontend_contract_export.py`；
- 生成物只能由脚本产出，**不得手改** `web/contracts/generated/**` 与 `web/contracts/schema/**`；
- 不改 `deeptutor/api/routers/**`（新路由属于 F09）；
- 不引入新依赖；
- **本环境暂不能跑前端**（无 Node），所以前端三个文件改了但无法验证——必须在「变更记录」里如实写「未执行」，不得写「通过」；
- 不提交。

## 验收

可执行部分（后端）：

```bash
cd /root/DeepTutor
.venv/bin/pip install -q -r requirements/core.txt      # 仅用于让导出可 import；项目既有依赖
.venv/bin/python -m deeptutor.api.contracts.export
.venv/bin/python -m pytest tests/api/test_frontend_contract_export.py -q
grep -c '"plan_draft"' web/contracts/schema/turn-protocol.json
```

达标：导出命令输出 `Updated frontend contracts: ...`（或第二遍无输出）、pytest 全绿、`grep` 计数 `>= 1`。

需要 Node 的部分（**当前环境不可执行，只登记不冒充通过**）：

```bash
cd web && npm run contracts:check && npm run typecheck && npm run test:unit
```

测试必须新增一条断言：`render_contracts()["turn-protocol.json"]` 的 `StreamEventType` 枚举值里包含 `plan_draft`。

## 交付

- 产出物：上述 8 个文件（其中 3 个为生成物）
- 证据：导出命令输出 + pytest 输出 + grep 计数 + 一行「前端未验证（无 Node）」说明
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交


---

## 裁撤理由（2026-11-01，F10 之后）

**事实**：F08 的 `PlanWeekTool` 已经把 `{"plan_draft_id": ...}` 放进 `ToolResult.metadata`
（`deeptutor/tools/planner_tools.py`），而 `tool_result` 是**既有**事件类型，metadata 会原样带到流里。
项目里已有同样的先例：`deeptutor/tools/mastery_nav.py` 的 `HANDOFF_META_KEY = "mastery_handoff"`，
前端用 `web/lib/tool-event.ts` 的 `toolResultPayload()` 把卡片读出来。

**结论**：再引入一个 `plan_draft` 事件类型，会让同一件事有两个真相来源（metadata 里有 id，
事件里也有 id），而且要多改两处枚举、重新导出契约、同步两处前端白名单——换来的只有重复。

**因此**：
1. 删除「新增事件类型」这一条，F13 改为读 `tool_result.metadata.plan_draft_id`；
2. 本文件状态记 ✗，`web/contracts/schema/*` 与 `web/contracts/generated/*` **不因本功能而变化**
   （它们仍会因 F09/F10 新增的 HTTP 路由而更新，那是另一件事）；
3. 契约工具链（导出 / 生成 / `--check`）本身是真实需要的，已在 F09/F10 打通并验证。

**教训**：先写文档时，「如何在 UI 里表现」容易假定一个新协议事件，而真正的项目惯例往往是把结构化提示
挂在工具结果的 metadata 上。落地前先 grep 一遍既有先例，能省掉一整轮协议改动。
