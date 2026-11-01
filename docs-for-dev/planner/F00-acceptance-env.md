# F00 · 验收环境

| 项 | 值 |
| --- | --- |
| 状态 | ☑ 已验收（2026-11-01，无代码改动，故无 commit） |
| 里程碑 | M0 |
| 依赖 | — |
| 回滚 | 无需回滚（不产生仓库文件；`.venv/` 已被 `.gitignore` 忽略） |

## 背景

本机是 WSL + 裸 python3：`python3 -c "import fastapi"` 报 `ModuleNotFoundError`，`pip3` 与 `ensurepip` 都不存在，`command -v node` 无输出。任何后端验收命令在环境修好之前都跑不了，所以先把「能跑 pytest」这件事本身当成一个功能做完。

**实测发现（重要）**：`tests/conftest.py` 在导入期就会拉起整个应用图（`runtime.stream_bus`、`multi_user.paths` → `services.llm` → `httpx` 等），所以「只装 pytest + pydantic」跑不了任何测试——哪怕被测模块本身只依赖标准库。venv 必须装项目运行依赖。

## 你的任务

在仓库根建一个**本地** venv 并装到「能让 `tests/` 收集并运行」的程度：pytest 全家桶 + 项目 core / server / calendar 三组依赖。不改任何纳入版本库的文件。

已完成（本会话）：

```bash
python3 -m venv --without-pip .venv
curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
.venv/bin/python /tmp/get-pip.py --no-warn-script-location
.venv/bin/python -m pip install pytest pytest-asyncio pydantic pyyaml python-dateutil croniter
.venv/bin/python -m pip install -r requirements/core.txt -r requirements/modules/server.txt -r requirements/modules/calendar.txt
```

不装 `requirements/full.txt`（含 parsing / knowledge 等重模块），规划相关的测试用不到。

## 必读

| 文件 | 为什么 |
| --- | --- |
| [README.md](README.md) | 全局约定与「验收环境现状」 |
| `tests/conftest.py` | 解释为什么最小依赖不够：导入期就加载应用图 |
| `docs-for-dev/TESTING.md` | 项目对测试分层与命令的既有约定 |
| `pyproject.toml`（`[tool.pytest.ini_options]`） | testpaths、markers、`--strict-markers` 等约束 |
| `.gitignore` | 确认 `.venv/` 不进版本库 |

## 硬性约束

- 只在仓库根创建 / 使用 `.venv/`，**不写任何纳入版本库的文件**；
- 不装全量依赖，只装上面列出的三组；
- 不修改 `pyproject.toml`、不改 CI 配置、不提交；
- 不尝试安装 Node（前端验收在 F04 之后再处理）。

## 验收

```bash
cd /root/DeepTutor
.venv/bin/python -m pytest --version
.venv/bin/python -m pip -V
.venv/bin/python -c "import httpx, fastapi, pydantic, yaml, dateutil, croniter; print('deps ok')"
.venv/bin/python -m pytest tests/services/planner -q --collect-only | tail -3
```

达标状态：前三条退出码 0；第四条能收集到用例（此时 F01 若未实现则为 `no tests ran`，同样算通过）。

实测结果：`pytest 9.2.0` / `pip 26.2.2` / `deps ok`。

## 交付

- 产出物：仓库根 `.venv/`（本地目录，不提交）
- 证据：上述命令输出，已记入 `README.md` 的「变更记录」
- 结论：环境就绪；**前端（typecheck / vitest / contracts）仍不可执行**，因为本机没有 Node
