# F13 · 聊天渲染接入

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M2 |
| 依赖 | F11（F04 已裁撤：草稿 id 走 `tool_result.metadata`）|
| 回滚 | `git revert <sha>`。回滚后卡片不再出现（草稿仍在库中，可由 API 找回） |

**环境前置**：Node 22 + `npm ci`，否则只能静态检查。

## 背景

卡片做好了，还得让它在该出现的时候出现：模型调完 `plan_week`，聊天流里冒出这张卡；用户刷新页面，它还在；切走再切回，它仍能继续勾选。项目的聊天流靠 `StreamEvent` 重放，所以卡片状态必须能从事件里恢复，不能只存在内存里。

## 你的任务

只做一件事：识别 `plan_draft` 事件并把卡片渲染进消息流，且刷新后可恢复。

规则：

1. `tool_result` 事件的 `metadata.plan_draft_id` 存在 → 视为一次草稿产出，取 `GET /api/planner/drafts/{id}` 后渲染 `<PlanDraftCard>`；
   **不需要新的流事件类型**（见 [F04 的裁撤理由](F04-contracts.md)）：`tool_result` 是既有事件，metadata 原样到前端；
   读取方式照 `web/lib/tool-event.ts` 的 `toolResultPayload()`，与 `mastery_handoff` 卡同一套做法；
2. 卡片挂在**产生它的那条助手消息**下面，位置固定在消息正文之后、工具轨迹之前；
3. **同一会话只激活最新一个未 applied/undone 的草稿**；更早的草稿折叠成一行「已过期的草稿 · 查看」；
4. 重放：消息从历史加载时，事件里已有 `plan_draft_id` → 自动拉取草稿，状态与 when live 一致（已 applied 的显示结果，不显示勾选框）；
5. 拉取失败（草稿过期/被清理）→ 显示一行「草稿已过期」，不报错、不重试；
6. 卡片组件**不做**网络请求以外的状态管理（状态源在服务端），本地只存勾选草稿；
7. 不在流事件里塞整份草稿内容——只传 id，内容按需拉取（事件体积要小，重放才不卡）。

新增 `web/lib/planner-state.ts` 放「从事件里找草稿 id」「判断哪个是最新激活草稿」这类纯函数，便于单测。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F04-contracts.md](F04-contracts.md) | 已裁撤；读它的「裁撤理由」理解为什么改用 metadata |
| [F11-draft-card.md](F11-draft-card.md) | 卡片 props |
| [F08-plan-week.md](F08-plan-week.md) | 工具产出 `metadata.plan_draft_id` 的位置 |
| `web/features/chat/messages/ChatMessageList.tsx` | 消息与事件的渲染入口（`wait_for_input` 卡片在那里处理，照它的位置感） |
| `web/lib/ask-user-state.ts` | 本项目「从事件恢复交互状态」的既有范式 |
| `web/features/chat/model/protocol.ts` | `StreamEvent` 的 UI 视图类型 |

## 硬性约束

- 只写这 4 个文件：`web/features/chat/messages/ChatMessageList.tsx`、`web/lib/planner-state.ts`、`web/tests/plan-draft-chat.spec.tsx`、`web/lib/planner-api.ts`（若需补查询参数）；
- 不得修改 `web/contracts/**`（F04 已定稿）、不得修改卡片组件的业务规则（F11/F12）；
- 不得为卡片新增全局 store；不引依赖；
- 事件里不得携带草稿全文；
- 不提交。

## 验收

```bash
cd /root/DeepTutor/web
npm run test:unit -- tests/plan-draft-chat.spec.tsx
npm run typecheck && npm run contracts:check
```

测试必须覆盖：

1. 消息包含 `tool_result`（`metadata.plan_draft_id = "d1"`）→ 渲染出卡片，且只发一次 `GET /drafts/d1`；
2. 同一消息含两条草稿事件 → 只渲染最新一个，旧的那个折叠；
3. 历史重放（事件已存在、无流式过程）→ 卡片仍渲染；
4. 草稿接口 404 → 显示「草稿已过期」，不抛异常、不无限重试；
5. 草稿已 `applied` → 卡片显示结果状态，不显示勾选框；
6. `web/lib/planner-state.ts` 纯函数：给定多条事件，返回的「最新激活草稿 id」正确（含空态）。

## 交付

- 产出物：渲染接入 + 状态纯函数 + 测试
- 证据：命令输出（无 Node 则注明「未执行」）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
