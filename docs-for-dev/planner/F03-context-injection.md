# F03 · 上下文注入

| 项 | 值 |
| --- | --- |
| 状态 | ☐ 未开始 |
| 里程碑 | M0 |
| 依赖 | F02 |
| 回滚 | `git revert <sha>`。回滚后模型重新「看不见」任务与日历，不影响任何已存数据 |

## 背景

F02 只是把事实渲染成一段字符串，还没人读它。本功能把这段字符串接进系统提示词，让模型在**不动用任何工具**的情况下也能回答「我今天有什么」。这是后面所有工具调用能被正确使用的前提。

## 你的任务

只做一件事：新增一个 `planner_context` 提示词块并把它接进 chat 轮次的上下文装配。不做工具、不做卡片、不改 event 协议。

三处改动：

1. `deeptutor/core/context.py`：`UnifiedContext` 增加字段 `planner_context: str = ""`，并在类 docstring 的字段清单里补一行说明（照 `learning_journal_context` 的写法）。
2. `deeptutor/agents/loop/prompt_blocks.py`：在 `blocks()` 中，**紧跟 `learning_journal` 之后、`tools` 之前**插入
   `if context.planner_context: blocks.append(PromptBlock("planner", context.planner_context))`。
3. `deeptutor/services/session/turns/executor.py`：在 `learning_journal_context` 之后同风格构建

```python
planner_context = ""
if not side_chat_context and module_enabled("calendar"):
    try:
        from deeptutor.services.planner.context import load_planner_context
        from deeptutor.services.workspace.context import current_workspace_id

        planner_context = load_planner_context(current_workspace_id())
    except (OSError, ValueError):
        logger.exception("Unable to build planner context; continuing without it")
```

并作为 `planner_context=planner_context` 传给 `UnifiedContext(...)`（第 968 行附近那组参数）。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定 |
| [F02-planner-context.md](F02-planner-context.md) | 你要接的函数名与返回语义 |
| `deeptutor/core/context.py` | 字段清单与 docstring 的写法惯例 |
| `deeptutor/agents/loop/prompt_blocks.py`（`blocks()`，约 119–190 行） | 块顺序是设计，插入位置决定模型先读什么 |
| `deeptutor/services/session/turns/executor.py`（约 585–660、960–975 行） | `learning_journal_context` 的完整构建与传参范式 |

## 硬性约束

- 只写这 4 个文件：`deeptutor/core/context.py`、`deeptutor/agents/loop/prompt_blocks.py`、`deeptutor/services/session/turns/executor.py`、`tests/agents/test_planner_prompt_block.py`；
- 不改工具、不改路由、不改前端、不改 F02 的实现；
- 构建失败必须降级为 `""`（**不能让聚合异常打断整轮对话**）；
- **侧边聊天（`side_chat_context`）不注入**——边栏答疑不该被主会话的计划干扰；
- 非 chat 能力本轮不注入（`capability_name` 不在 `{"", "chat"}` 时跳过）；
- 不提交。

## 验收

前置（仅环境，非新增依赖）：

```bash
cd /root/DeepTutor
.venv/bin/pip install -q httpx          # prompt_blocks 的间接导入需要它，项目已有依赖
```

```bash
.venv/bin/python -m pytest tests/agents/test_planner_prompt_block.py -q
grep -n "planner_context" deeptutor/services/session/turns/executor.py
```

测试必须覆盖：

1. `UnifiedContext(planner_context="## 规划上下文\n今天是 X")` → `blocks(context=ctx, tool_manifest="")` 里存在名为 `planner` 的块，内容一致；
2. `planner_context=""` → 不存在 `planner` 块（空块不得出现）；
3. 顺序断言：`planner` 出现在 `learning_journal` 之后、`skills`/`sources` 之前；
4. `UnifiedContext` 的默认值仍是 `""`（构造不传该字段不报错）。

## 交付

- 产出物：三处改动 + 一个新测试文件
- 证据：pytest 全绿输出 + `grep` 输出（证明 executor 已接线）
- 通过后：本文件状态改 `☑ 已验收` + commit sha，然后提交
