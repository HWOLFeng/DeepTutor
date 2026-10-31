# 服务器清理计划：从“源码热修机”转为“镜像运行 + 数据节点”

日期：2026-10-31
适用主机：`8.134.12.162`（`/root/DeepTutor`）
目标镜像：`ghcr.io/hwolfeng/deeptutor:<tag>`（本 Fork 自定义镜像）

## 1. 结论

**方向合适，但要把角色定义清楚：**

> 这台服务器是**不可变运行节点 + 有状态数据节点**，不是“只拉 latest 的黑盒”。

它只负责：

1. 拉取指定 tag/digest 的自定义镜像；
2. 用固定 compose 启动容器；
3. 保存 `/app/data`（账号、聊天、知识库索引、练习记录、附件）；
4. 对外提供 nginx/TLS 反向代理。

它不再负责：

- Node/Python 源码构建；
- `npm ci`、`next build`、`pip install`；
- 通过 bind mount 覆盖运行时代码；
- 保存 `node_modules`、`.next`、`.venv`、`deploy/web-app`。

这样做的好处是部署可复现、攻击面小、不会出现“容器里跑的是哪版代码”的歧义。
代价是**必须保留回滚镜像、数据备份和 registry 可达性**，不能只留 `latest`。

## 2. 当前占用盘点

| 项目 | 当前占用 | 处理建议 |
| --- | ---: | --- |
| `/root/DeepTutor/web/node_modules` | 1.3 GB | 自定义镜像验证后删除 |
| Docker volume `deeptutor_web_node_modules` | 1.165 GB | 删除 |
| `deploy/web-app` | 115 MB | 自定义镜像含前端后删除 |
| `.venv` | 14 MB | 删除（且是 Python 3.6，本来不可用） |
| `node:22-bookworm-slim` 镜像 | 227 MB | 服务器不再构建前端后删除 |
| `redis:7.4-alpine` 镜像 | 39 MB | 当前 compose 未使用，删除 |
| `/tmp`、`/root/.npm`、`/root/.cache` | 约 40 MB | 清理临时文件 |
| 旧 `ghcr.io/hkuds/deeptutor:latest` | 2.39 GB | **先保留作回滚**，新镜像稳定后再删 |
| `/root/DeepTutor/data` | 128 MB | **必须保留**，并备份 |
| `/root/DeepTutor/deploy`（去掉 web-app） | 约 200 KB | 保留 compose/prompts/nginx/运维脚本 |
| `.git` + 源码（不含依赖） | 约 398 MB | 可保留压缩快照，删除工作树 |
| 磁盘总量 | 49 GB，已用 16 GB，可用 32 GB | 清理后预计释放 2.8–5.2 GB |

注意：`ghcr.io/hkuds/...` 与 `ghcr.nju.edu.cn/hkuds/...` 是**同一个 image ID**，删一个 tag 不会释放 2.39 GB；必须两个 tag 都不再引用且容器不再使用，才会真正释放。

## 3. 目标状态

```text
/root/DeepTutor/
├── data/                     # 必须，唯一有状态目录
├── deploy/
│   ├── compose.yml           # 固定镜像 tag/digest
│   ├── prompts/              # 可选：团队书籍提示词
│   └── server/               # nginx 配置备份
└── backups/                  # 数据备份（最好再异地一份）

Docker:
├── 当前镜像 ghcr.io/hwolfeng/deeptutor:<version>
├── 上一版镜像（回滚用）
└── 容器 deeptutor

主机:
├── nginx + TLS 证书
└── 定时备份 / 磁盘告警
```

不保留：`web/node_modules`、`web/.next`、`.venv`、`deploy/web-app`、`node`/`redis` 镜像、源码工作树、构建缓存。

## 4. 前置条件（缺一不可）

- [ ] Windows 已构建并推送自定义镜像；
- [ ] 服务器能 `docker pull` 到该镜像；
- [ ] 镜像内已包含前端、练习导入、题图、`practice-template` 技能；
- [ ] 已把当前容器健康状态、镜像 digest、compose 文件记录到变更单；
- [ ] 已完成一次可恢复的 `data` 备份；
- [ ] 已确认停机窗口（建议 10–20 分钟）。

## 5. 分阶段执行

### 阶段 0：备份与记录

不要直接 `tar` 正在写入的 SQLite。先停容器，再打包：

```bash
cd /root/DeepTutor
docker compose -f deploy/compose.yml stop

TS=$(date +%F-%H%M)
mkdir -p /root/backups/deeptutor/$TS

tar --numeric-owner -czf /root/backups/deeptutor/$TS/data.tar.gz data
tar --numeric-owner -czf /root/backups/deeptutor/$TS/deploy.tar.gz deploy

cp /etc/nginx/conf.d/deeptutor.conf /root/backups/deeptutor/$TS/
cp /etc/nginx/conf.d/00-upgrade.conf /root/backups/deeptutor/$TS/
cp /etc/nginx/conf.d/01-login-limit.conf /root/backups/deeptutor/$TS/
cp /etc/pki/tls/certs/deeptutor.crt /root/backups/deeptutor/$TS/
cp /etc/pki/tls/private/deeptutor.key /root/backups/deeptutor/$TS/

docker image inspect ghcr.io/hkuds/deeptutor:latest \
  --format '{{json .RepoDigests}}' > /root/backups/deeptutor/$TS/old-image.json

sha256sum /root/backups/deeptutor/$TS/* > /root/backups/deeptutor/$TS/SHA256SUMS
```

把备份复制到另一台机器或对象存储；**同盘备份不算备份**。

恢复演练至少做一次：

```bash
mkdir -p /tmp/restore-test
tar -xzf /root/backups/deeptutor/$TS/data.tar.gz -C /tmp/restore-test
du -sh /tmp/restore-test/data
```

### 阶段 1：切换自定义镜像

先把旧镜像打回滚 tag：

```bash
docker tag ghcr.io/hkuds/deeptutor:latest deeptutor:rollback
```

修改 `deploy/compose.yml`：

```yaml
services:
  deeptutor:
    image: ghcr.io/hwolfeng/deeptutor:2026-10-31
    volumes:
      - /root/DeepTutor/data:/app/data
      - /root/deeptutor-vault/team:/vaults/team
      - /root/team-docs:/team-docs:ro
```

删除所有源码/前端热修挂载：

- `deploy/web-app:/app/web`
- `deeptutor/book/context.py`
- `deeptutor/agents/video_lesson/runtime.py`
- `deeptutor/skills/builtin/practice-template`
- `deeptutor/services/practice`
- `sqlite_store.py`、`practice.py`、`question_notebook.py`

如果团队书籍提示词仍要保留，可继续挂载 `deploy/prompts/zh`；否则也应打进镜像，避免双份来源。

启动：

```bash
cd /root/DeepTutor/deploy
docker compose pull
docker compose up -d --force-recreate
docker compose ps
curl -fsS http://127.0.0.1:3782/ >/dev/null && echo frontend-ok
curl -fsS http://127.0.0.1:8001/health/ready && echo backend-ok
```

### 阶段 2：观察期（建议 24–48 小时）

先不要删旧镜像和 `deploy/web-app`。验证：

- [ ] 登录、退出、成员权限；
- [ ] 题库/错题本历史数据；
- [ ] PDF/DOCX 导入、题图显示；
- [ ] 学习记录侧栏聚合；
- [ ] 视频讲解首次运行（npm/Remotion）；
- [ ] 成员读取管理员共享书籍；
- [ ] 知识库检索 `/vaults/team`；
- [ ] 重启容器后数据仍在；
- [ ] 磁盘、内存、容器日志无异常。

### 阶段 3：清理构建产物

确认稳定后执行：

```bash
cd /root/DeepTutor

# 前端依赖与本地构建缓存
rm -rf web/node_modules web/.next
docker volume rm deeptutor_web_node_modules

# 本地前端 standalone 产物
rm -rf deploy/web-app

# 无用的 Python 环境
rm -rf .venv

# 服务器不再本地构建前端/未使用 redis
docker image rm node:22-bookworm-slim
docker image rm redis:7.4-alpine

# 临时文件与缓存
rm -rf /tmp/deeptutor-* /root/.npm /root/.cache
rm -f /root/pull.log /root/pull2.log
```

阶段 3 完成后，`/root/DeepTutor` 应从 2.0 GB 降到约 500 MB（主要是 `data/`、`deploy/`、源码/`.git`）。

### 阶段 4：移除源码工作树（可选）

如果确定服务器永远不再构建，可以只保留数据与部署配置：

```bash
# 先做一份源码快照，放备份目录，不进 data
cd /root/DeepTutor
git archive --format=tar.gz -o /root/backups/deeptutor/source-$(git rev-parse --short HEAD).tar.gz HEAD

# 删除构建/源码目录；保留 data、deploy、backups
rm -rf web deeptutor deeptutor_cli tests docs-for-dev packages packaging scripts requirements .git
# 其余根目录构建文件（Dockerfile、compose*.yaml、pyproject.toml 等）
# 请逐项确认后再删，不要直接照抄省略号。
```

**不建议第一阶段就做这步。** 至少保留一个压缩源码快照，并确认：
- 新镜像可以独立启动；
- 回滚镜像可用；
- 数据备份可恢复；
- 未来所有修复都走 Windows/CI 构建，不再服务器热修。

### 阶段 5：旧镜像处理

新镜像稳定 1–2 周后：

```bash
# 保留当前版本 + 上一版，删除更老的
docker image rm ghcr.io/hkuds/deeptutor:latest
docker image rm ghcr.nju.edu.cn/hkuds/deeptutor:latest
```

如果 `deeptutor:rollback` 仍指向旧 ID，先确认不需要回滚，再删除 rollback tag。

## 6. 不建议删除的内容

| 路径 | 原因 |
| --- | --- |
| `/root/DeepTutor/data` | 账号、密钥、聊天、知识库索引、练习记录、附件；无法从镜像恢复 |
| `/root/DeepTutor/deploy/compose.yml` | 没有它就无法重建容器 |
| `/root/DeepTutor/deploy/prompts`（若使用） | 团队书籍提示词，不在镜像里 |
| `/root/deeptutor-vault/team` | 知识库源 vault |
| `/root/team-docs` | 原始资料；重建索引需要 |
| `/etc/nginx/conf.d/*deeptutor*` | 公网入口、登录限速、WebSocket 升级 |
| `/etc/pki/tls/.../deeptutor.*` | TLS 证书与私钥 |
| 上一版镜像 | 回滚唯一快速路径 |
| `data/knowledge_bases` | 索引构建成本高，丢失后要重新跑 embedding |

## 7. 长期运维建议

1. **镜像固定 tag/digest**，不用 `latest` 自动漂移；部署前记录 digest。
2. **每次升级前备份 `data`**；升级后跑健康检查和导入冒烟测试。
3. **保留两版镜像**：当前 + 上一版；删除更老的。
4. **数据备份异地化**：本机 + 对象存储/另一台机器；至少每日一次。
5. **磁盘告警**：`/root/DeepTutor/data`、`/var/lib/docker`、`/` 使用率超过 80% 告警。
6. **日志轮转**：compose 已限制 `20m × 5`；定期检查。
7. **基础镜像月度重建**：即使业务代码不变，也重建以获取 Python/Node/系统安全更新。
8. **Registry 可用性**：GHCR 不可达时，保留 `docker save` 的 tar 作为应急；或推一份到阿里云 ACR。
9. **不要自动 watchtower**：有状态服务升级必须人工确认 + 备份。
10. **记录恢复演练**：每季度从备份恢复一次到临时目录，确认可读。

## 8. 风险与取舍

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| GHCR 拉取失败/限流 | 无法部署新版本 | 公开包、保留上一版镜像、准备 `docker save` tar |
| 误删 `data` | 用户数据永久丢失 | 停容器备份、异地副本、恢复演练 |
| 旧镜像删除过早 | 无法快速回滚 | 保留 `deeptutor:rollback` 1–2 周 |
| 只保留 `latest` | 无法复现问题 | 固定版本 tag + digest |
| 服务器无源码 | 紧急热修不便 | 源码快照 + Windows/CI 重新构建；不要现场改容器 |
| 数据盘写满 | SQLite/索引损坏 | 磁盘告警、日志轮转、定期清理缓存 |
| 证书过期 | 公网不可用 | 到期提醒/自动续期 |

## 9. 建议的执行顺序

1. 先在 Windows 构建并推送自定义镜像；
2. 在服务器备份 `data` + `deploy` + nginx/certs；
3. 切换 compose 到自定义镜像；
4. 观察 24–48 小时；
5. 删除 `node_modules`、volume、`web-app`、`.venv`、node/redis 镜像；
6. 再决定是否删除源码工作树；
7. 1–2 周后删除旧镜像，只保留当前 + 上一版；
8. 配置备份、磁盘告警和季度恢复演练。

**一句话：服务器只做“拉镜像、起容器、存数据”是合适的；但必须同时保留“可回滚镜像、可恢复数据、可重建 compose”这三样，才算完整。**
