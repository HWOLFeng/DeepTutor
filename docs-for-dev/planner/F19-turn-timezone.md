# F19 · 规划上下文的读者时区

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，`06e8c05b`）|
| 里程碑 | M3（补漏） |
| 依赖 | F03 |
| 回滚 | `git revert <sha>`。回滚后「今天」退回宿主机时区（见 F03 的已知缺口） |

## 背景

F03 把规划块接进了提示词，但**它不知道读者在哪个时区**：turn payload 里没有这个字段，于是
`load_planner_context` 退回宿主机时区（`TZ` / `/etc/timezone` / `/etc/localtime`）。本机实测宿主机是
`Asia/Shanghai`，但一个在 UTC 服务器上跑的应用、读者在 `Asia/Shanghai`，凌晨 0–8 点就会把「今天」
说成昨天——规划块里最贵的一句话错了。

好消息是不需要改线协议：`TurnRequest.config` 是自由 dict，客户端已经在往里塞 `linked_task_ids`。

## 你的任务

只做一件事：让浏览器把读者时区随每一轮上报，规划块用它而不是宿主时区。

1. **客户端**：在拼 `start_turn` 的 `config` 处（已有 `config = { ...(config ?? {}), linked_task_ids: draftTaskIds }` 的那一行附近）附加
   `timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || undefined`。取不到就不传，不要传空串。
2. **执行侧**：`executor.py` 里构建规划块之前，从 `payload.get("config") or {}` 取 `timezone`，用
   `zoneinfo.ZoneInfo` 校验；非法值**忽略**（记一条 debug 日志），绝不因此让本轮失败。
3. **传递**：把校验后的值作为 `tzid=` 传给 `load_planner_context(..., tzid=...)`（该参数 F02 已有）。
   实现上这一层收在 `deeptutor/services/planner/context.py` 的 `build_turn_planner_context(config=..., workspace_id=..., session_id=...)`
   里，executor 只留一次调用——否则要测「时区是否传对」就得搭起一整套 turn 运行环境。
4. **优先级**：请求里的时区 > 宿主时区 > `UTC`。宿主兜底逻辑保持在 `context.local_zone_name()` 里不动。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [F03-context-injection.md](F03-context-injection.md) | 现在的注入点与「宿主时区」这条已知缺口 |
| [F02-planner-context.md](F02-planner-context.md) | `load_planner_context(tzid=...)` 与 `local_zone_name()` 的语义 |
| `deeptutor/core/turn_request.py`（`TurnRequest`） | `config` 是 `dict[str, Any]`，因此不必动线协议 |
| `deeptutor/services/session/turns/executor.py`（规划块构建处，约 600–625 行） | 你要改的那 4 行 |
| `web/features/chat/components/ChatWorkspace.tsx`（约 2053 行） | 客户端拼 `config` 的唯一位置 |
| `web/lib/usage-statistics.ts` | 项目里取浏览器时区的既有写法，照抄它 |

## 硬性约束

- 只写这 4 个文件：`web/features/chat/components/ChatWorkspace.tsx`、`deeptutor/services/session/turns/executor.py`、`tests/agents/test_planner_turn_timezone.py`、`deeptutor/services/planner/context.py`；
- 在 `context.py` 里**新增两个小函数**（`turn_zone` 校验 + `build_turn_planner_context` 组装）是本次的偏离：原计划只加一个校验函数，但那样 executor 里要写两行取值再转发，测试就得搭整个 turn 运行环境。以「可测」换「少一个函数」，判定为值得；
- **不得改线协议**（`turn-protocol.json` / `openapi.json` / 生成物）；`config` 是自由字段，改协议是过度设计；
- 非法时区必须无副作用地忽略：不得抛错、不得清空规划块；
- 不得把时区写进会话偏好（它是每轮的属性，不是持久设置）；
- 不引依赖（`zoneinfo` 是标准库）、不提交。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest tests/agents/test_planner_turn_timezone.py -q
.venv/bin/python -m pytest tests/services/planner -q
```

实测结果：`tests/agents/test_planner_turn_timezone.py` → `11 passed`；`tests/services/planner` + 两个 agents 规划用例
合计 `39 passed`；`tests/agents` 全量 `511 passed, 13 skipped`。

`tests/services/session` 有 8 个失败，**经 `git stash -u` 基线对比确认为改动前既有失败**
（`WorkspaceError: The selected workspace folder does not exist.`，与本功能无关），不在本功能修复范围内。

测试必须覆盖（用打桩的 `load_planner_context` 断言收到的 `tzid`）：

1. `config={"timezone": "America/New_York"}` → 收到 `tzid="America/New_York"`；
2. 没有 `config` → 收到 `None`（由 `context` 兜底到宿主时区）；
3. `config={"timezone": "Mars/Olympus"}` → 不抛错，收到 `None`；
4. `config={"timezone": ""}` → 收到 `None`；
5. side chat / 非 chat 能力 → 根本不构建（沿用 F03 的门控，本功能不改变它）。

前端部分：`npm run test:unit` 与 `npm run typecheck` 需要 Node，当前环境不可执行，照 F11 的规矩如实标注。

## 交付

- 产出物：客户端 1 处 + 执行侧 1 处 + planner 两个小函数 + 测试
- 证据：pytest 输出；前端部分若未执行须写明
- 通过后：状态改 `☑ 已验收` + commit sha，然后提交

## 后续修正（2026-11-01，真实使用中暴露，`d6278c16`）

**这个功能上线后是坏的：每一条消息都发不出去。**

客户端按要求给每个请求加上 `config.timezone`，而服务端每种能力的 config 校验都是
`extra="forbid"`（`EmptyConfig`、`DeepQuestionRequestConfig`、`VisualizeRequestConfig`、
`DeepResearchRequestConfig`、`MathAnimatorRequestConfig`、`AudioOverviewRequestConfig`）。
于是 `start_turn` 在准备阶段抛：

```
Invalid chat config: timezone: Extra inputs are not permitted
```

前端把 `start_turn_rejected` 渲染成**固定一句**「无法连接到服务器，请检查网络后重试」
（`web/features/chat/components/ChatWorkspace.tsx`），所以用户看到的是网络问题，实际是校验拒绝。
服务端那条 `except` 分支当时也不打日志——两边都看不见原因。

**为什么验收没抓到**：`tests/agents/test_planner_turn_timezone.py` 是打桩 `load_planner_context`、
直接传 `config` 字典进执行侧，**从未经过 `validate_capability_config`** 这道真正的入口；而前端那一半
（本文件第 74 行）当时没有 Node，连 `typecheck` 都没跑。两条证据加起来，恰好绕开了唯一会失败的环节。

**修法**：新增 `ReaderTimeZone` 基类（`deeptutor/runtime/capability_catalog.py`）声明
`timezone: str | None`，所有能力 config 继承它；`tests/runtime/test_reader_timezone_configs.py`
**遍历能力目录**断言「每个能力的 config 都认得 timezone」——它当场找出两个没人记得的
（audio overview、research），这正是不写死模型名的价值。

`timezone` 保持 Optional 且为空时不落盘，请求快照不会多出 `timezone: ""`。

**仍未解决**：被拒绝的那条消息会卡在「未发送」。它的 submission 预留已经写进库，
而重试时客户端已经知道 session id，payload 与首次不同 → 摘要不同 → 重试被
`This submission ID belongs to a different request or workspace.` 挡住。目前只能「丢弃未发送的草稿」
再重发。要真正让「重试」可用，得让客户端按首次的 request snapshot 原样重放，这是独立的一件事。
