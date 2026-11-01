# F19 · 规划上下文的读者时区

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
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

- 只写这 4 个文件：`web/features/chat/components/ChatWorkspace.tsx`、`deeptutor/services/session/turns/executor.py`、`tests/agents/test_planner_turn_timezone.py`、必要时 `deeptutor/services/planner/context.py`（只允许新增一个校验小函数）；
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

测试必须覆盖（用打桩的 `load_planner_context` 断言收到的 `tzid`）：

1. `config={"timezone": "America/New_York"}` → 收到 `tzid="America/New_York"`；
2. 没有 `config` → 收到 `None`（由 `context` 兜底到宿主时区）；
3. `config={"timezone": "Mars/Olympus"}` → 不抛错，收到 `None`；
4. `config={"timezone": ""}` → 收到 `None`；
5. side chat / 非 chat 能力 → 根本不构建（沿用 F03 的门控，本功能不改变它）。

前端部分：`npm run test:unit` 与 `npm run typecheck` 需要 Node，当前环境不可执行，照 F11 的规矩如实标注。

## 交付

- 产出物：客户端 1 处 + 执行侧 1 处 + 测试
- 证据：pytest 输出；前端部分若未执行须写明
- 通过后：状态改 `☑ 已验收` + commit sha，然后提交
