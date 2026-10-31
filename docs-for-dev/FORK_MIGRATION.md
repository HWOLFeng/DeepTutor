# Fork 迁移与生产部署交接

本文记录本仓库从上游 `HKUDS/DeepTutor` 迁出后，哪些改动属于源码、哪些属于运行时数据，
以及如何在 Windows 上重新构建镜像、再把镜像部署回当前 Linux 服务器。

- 上游：<https://github.com/HKUDS/DeepTutor>
- Fork：<https://github.com/HWOLFeng/DeepTutor>
- 当前分支：`main`
- 当前生产容器：`deeptutor`，镜像 `ghcr.io/hkuds/deeptutor:latest`
- 当前生产数据：`/root/DeepTutor/data`

> 重要：当前生产环境并不是“只跑官方镜像”。官方镜像不含本 Fork 的练习导入、题图展示、
> 教材共享读取等修复，因此**必须使用本仓库构建的自定义镜像**，不能继续 `pull` 上游
> `ghcr.io/hkuds/deeptutor:latest` 后直接替换。

## 1. 提交范围

### 1.1 必须提交（源码与测试）

| 路径 | 内容 |
| --- | --- |
| `deeptutor/services/practice/document_import.py` | PDF/DOCX 富文本、扫描页视觉识别、多图成组导入、图片压缩、题图裁剪/复核/SVG 重绘 |
| `deeptutor/services/practice/importing.py` | xlsx 行锚定图片、data URI、图片数量上限 |
| `deeptutor/services/practice/quality.py` | 答案归一化、错题判定、空位还原、重复题指纹 |
| `deeptutor/services/practice/verify.py` | 确定性校验：算式求值、等式链、选择题结构、正方体展开图 |
| `scripts/eval_practice_ocr.py` | 真实照片识别评测（题数/答案/泄漏/重复/图形） |
| `scripts/clean_practice_import.py` | 按批次清理导入（备份、题图、孤立附件） |
| `scripts/setup_chromium.sh` | 一次性下载 UI 测试用 Chromium 到 `data/.tools/`（不入 git） |
| `data/eval/practice-ocr/raw/` | 三张样图（语文填空、混合运算、正方体展开图） |
| `deeptutor/services/practice/storage.py` | 导入时持久化题图、重复导入刷新题图 URL |
| `deeptutor/services/session/sqlite_store.py` | `question_images_json` 建表/迁移/序列化、学习侧栏按导入文件聚合并返回 `material_id` |
| `deeptutor/api/routers/practice.py` | PDF/DOCX/图片异步预览、多图成组、题图写入 AttachmentStore |
| `deeptutor/utils/document_images.py` | 导入图片的 WebP 归一化、长边限制与无损优先压缩 |
| `deeptutor/services/llm/capabilities.py` | `deepseek-flash` 等模型的多模态能力覆盖 |
| `deeptutor/api/routers/question_notebook.py` | API 返回 `question_images` |
| `deeptutor/skills/builtin/practice-template/` | 内置技能：PDF/Word → 练习模板 xlsx，含 schema 参考 |
| `deeptutor/book/context.py` | 成员读取管理员共享书籍时回退到 admin 书库 |
| `deeptutor/agents/video_lesson/runtime.py` | 容器内 npm 缓存改到可写目录 |
| `web/components/common/QuestionFigures.tsx` | 题图组件 |
| `web/components/learning/practice/PracticeImport.tsx` | 导入面板接受 PDF/DOCX/多张图片、上传前压缩、显示题图缩略图 |
| `web/components/learning/practice/PracticeGroupSession.tsx` | 学习侧栏聚合文件直接开始整组练习 |
| `web/components/learning/practice/PracticePage.tsx` | `?group=` 进入整组练习、按文件筛选题库与提示条 |
| `web/components/learning/practice/PracticeSession.tsx` | 练习会话展示题图 |
| `web/components/space/question-bank/QuestionCard.tsx` | 题库卡片展示题图 |
| `web/components/sidebar/LearningNavigation.tsx` | 学习记录侧栏标题回退与数量徽标 |
| `web/lib/learning-dashboard.ts` | 练习聚合行、分组练习链接、统计按题目数计数 |
| `web/lib/notebook-api.ts`、`web/lib/practice-api.ts` | 题图类型 |
| `web/contracts/schema/openapi.json`、`web/contracts/generated/api.ts` | API 契约同步 |
| `web/locales/en/app.json`、`web/locales/zh/app.json` | 导入提示文案 |
| `web/app/layout.tsx` + `web/app/fonts/` | 本地 Geist/Lora 字体，构建不再依赖 Google Fonts |
| `tests/api/test_practice.py` | 练习导入/多图成组/图片压缩/题图/聚合测试 |
| `tests/utils/test_document_images.py` | 图片压缩与无损路径测试 |
| `tests/services/llm/test_capabilities.py` | 模型视觉能力表测试 |
| `web/tests/practice-group.spec.tsx`、`web/tests/practice-workflow.spec.tsx`、`web/tests/learning-dashboard.test.ts` | 分组练习、多图导入与聚合链接测试 |
| `tests/services/skill/test_practice_template_skill.py` | 技能测试 |
| `tests/book/test_context.py`、`tests/agents/video_lesson/test_runtime.py` | 两项修复的回归测试 |
| `NOTICE` | 非官方 Fork 声明与修改说明 |
| `Dockerfile`、`.dockerignore` | 生产镜像携带 `LICENSE`/`NOTICE`/第三方声明 |
| `.gitignore` | 忽略部署产物、团队部署目录 |

### 1.2 不要提交（运行时数据 / 本机产物）

| 路径 | 原因 | 迁移方式 |
| --- | --- | --- |
| `data/` | 全部业务状态：账号、auth_secret、API Key、聊天记录、用户工作区、知识库、附件、练习记录 | 单独备份/rsync，绝不能进 Git |
| `deploy/` | 团队生产配置：真实路径、PRD、成员脚本、nginx、构建产物、截图 | 单独备份；只在服务器上保留 |
| `deploy/web-app/` | 当前服务器本地构建的前端 standalone 产物 | 自定义镜像会把前端打进镜像；不再需要该挂载 |
| `web/node_modules/`、`web/.next/` | 依赖/构建缓存，可由 `npm ci` 重建 | 新机器重新安装/构建 |
| `.venv/` | 当前主机上的临时 Python 环境（且为 Python 3.6，不可用于本项目） | 新机器按 `requirements/full.txt` 重建 |
| `.env`、`data/user/settings/*.json` | 含密钥/提供商配置 | 通过 `data/` 备份迁移 |
| `*.log`、`data/shots/`、`deploy/data-shots/` | 日志/调试截图 | 不需要 |
| 证书私钥 `/etc/pki/tls/private/deeptutor.key` | 服务器证书 | 单独安全备份，不进 Git |

### 1.3 “之后安装”的东西

这些不是源码改动，拉取仓库后按需安装：

| 项目 | 什么时候需要 | 命令/说明 |
| --- | --- | --- |
| Python 依赖 | 不用 Docker、直接源码运行时 | `python -m pip install -r requirements/full.txt && python -m pip install -e . --no-deps` |
| Node.js 22 + `web/node_modules` | 本地开发前端或宿主机构建前端时 | `cd web && npm ci --legacy-peer-deps` |
| Docker 镜像 | 生产部署 | 在 Windows/CI 构建并推送到 GHCR，服务器只 pull |
| `practice-template` 技能 | 无需安装 | `deeptutor/skills/builtin/` 下的内置技能会随源码/镜像自动发现 |
| 字体 | 无需安装 | `web/app/fonts/` 已随仓库提交，`next/font/local` 使用 |
| 团队书籍提示词 | 只有仍要“小学内容不撑篇幅”的约束时需要 | `deploy/prompts/zh/*.yaml` 属于部署配置，单独备份或打进自定义镜像 |
| Obsidian vault / 原始资料 | 知识库重新索引时需要 | `/root/deeptutor-vault/team`、`/root/team-docs` |

## 2. Windows 上构建自定义镜像

前置：Docker Desktop（Linux containers）、约 20 GB 空闲磁盘、可访问 Docker Hub / GHCR。
Windows 侧从零搭建环境（WSL2 + Ubuntu + Docker Engine，或原生 venv 回退）的完整步骤、
代理与镜像源配置、常见坑，见 [WINDOWS_SETUP.md](WINDOWS_SETUP.md)。
当前镜像约 2.4 GB，构建峰值内存建议 4 GB 以上。

```powershell
git clone https://github.com/HWOLFeng/DeepTutor.git
cd DeepTutor

# 首次构建建议用 buildx；镜像名必须全小写
# --target production 是必须的：Dockerfile 的最后一个 stage 是 development
# （带 web/node_modules，约 6.9 GB），生产镜像约 3.1 GB。
docker buildx build `
  --platform linux/amd64 `
  --target production `
  -t ghcr.io/hwolfeng/deeptutor:latest `
  --push .
```

构建默认走国内源（apt 用 aliyun、pip 用 aliyun、npm 用 npmmirror、rustup 用
USTC）；在其他地区构建时用 `--build-arg` 覆盖，例如
`--build-arg APT_MIRROR=deb.debian.org --build-arg PIP_INDEX_URL=https://pypi.org/simple
--build-arg NPM_REGISTRY=https://registry.npmjs.org`。这些参数只影响构建过程，
不会写进运行环境。

如果暂时不推 GHCR，也可以先构建到本地再导出：

```powershell
docker build --platform linux/amd64 -t deeptutor-fork:latest .
docker save deeptutor-fork:latest -o deeptutor-fork.tar
# 再把 tar 传到服务器：docker load -i deeptutor-fork.tar
```

推 GHCR 需要在 GitHub 创建 Personal Access Token（至少 `write:packages`）：

```powershell
$env:GHCR_TOKEN | docker login ghcr.io -u HWOLFeng --password-stdin
```

构建完成后确认镜像里确实有本次改动：

```powershell
docker run --rm --entrypoint sh ghcr.io/hwolfeng/deeptutor:latest -c `
  "ls /app/deeptutor/skills/builtin/practice-template && grep -n question_images /app/deeptutor/services/session/sqlite_store.py | head"
```

## 3. 服务器部署

### 3.1 服务器最终只需要保留什么

运行时最小集合：

| 必须保留 | 说明 |
| --- | --- |
| `/root/DeepTutor/data` | **最重要**。账号、密钥、聊天、知识库、附件、练习记录全在这里 |
| `/root/deeptutor-vault/team` | 团队 Obsidian vault（如果知识库仍引用 `/vaults/team`） |
| `/root/team-docs` | 待入库原始资料（如果仍要重新索引） |
| `/root/DeepTutor/deploy` | 生产 compose、提示词覆盖、nginx 配置、运维脚本 |
| `/etc/nginx/conf.d/deeptutor.conf` 等 | 公网 TLS 反代配置 |
| `/etc/pki/tls/certs/deeptutor.crt`、`/etc/pki/tls/private/deeptutor.key` | 证书与私钥 |
| Docker + Docker Compose | 运行自定义镜像 |

**不需要**保留：`web/node_modules`、`web/.next`、`.venv`、`deploy/web-app`、源码 bind mount 所依赖的
`deeptutor/**` 文件。自定义镜像已经包含后端修复、练习导入、技能和前端构建产物。

但“只 pull 镜像 + 配 volume”成立的前提是：

1. 镜像由本 Fork 构建，而不是上游 `ghcr.io/hkuds/deeptutor:latest`；
2. compose 里删掉所有源码/`deploy/web-app` bind mount；
3. `data`、vault、team-docs 三个数据卷仍然挂载；
4. 团队提示词如果要保留，必须打进镜像或继续挂载 `deploy/prompts`。

### 3.2 compose 需要改什么

把 `image:` 改成自定义镜像，并删除以下当前用于“热修”的挂载：

```yaml
services:
  deeptutor:
    image: ghcr.io/hwolfeng/deeptutor:latest
    volumes:
      - /root/DeepTutor/data:/app/data
      - /root/deeptutor-vault/team:/vaults/team
      - /root/team-docs:/team-docs:ro
      # 可选：仍要团队书籍提示词时保留；否则删除
      # - /root/DeepTutor/deploy/prompts/zh:/app/deeptutor/book/prompts/zh:ro
    environment:
      - TZ=Asia/Shanghai
      - NPM_CONFIG_CACHE=/app/data/.cache/npm
      - NPM_CONFIG_UPDATE_NOTIFIER=false
```

要删除的挂载（自定义镜像已包含）：

- `/root/DeepTutor/deploy/web-app:/app/web`
- `/root/DeepTutor/deeptutor/book/context.py:...`
- `/root/DeepTutor/deeptutor/agents/video_lesson/runtime.py:...`
- `/root/DeepTutor/deeptutor/skills/builtin/practice-template:...`
- `/root/DeepTutor/deeptutor/services/practice:...`
- `/root/DeepTutor/deeptutor/services/session/sqlite_store.py:...`
- `/root/DeepTutor/deeptutor/api/routers/practice.py:...`
- `/root/DeepTutor/deeptutor/api/routers/question_notebook.py:...`

然后：

```bash
cd /root/DeepTutor/deploy
docker compose pull
docker compose up -d --force-recreate
docker compose ps
curl -fsS http://127.0.0.1:3782/ >/dev/null && echo frontend-ok
curl -fsS http://127.0.0.1:8001/health/ready && echo backend-ok
```

### 3.3 迁移 `data` 时的所有权

当前 `data` 属于 `1000:1000`（`admin`），容器内 `deeptutor` 也是 UID 1000。迁移时保留数字所有者：

```bash
rsync -aH --numeric-ids /root/DeepTutor/data/ user@new-host:/root/DeepTutor/data/
```

如果新机器 UID 不同，再执行：

```bash
chown -R 1000:1000 /root/DeepTutor/data
```

## 4. 当前服务器的清理判断

当前服务器上，下列内容是“历史部署方式”的产物，自定义镜像上线后可以删除：

- `deploy/web-app/`：前端 standalone 产物，已打进镜像后不再挂载；
- `web/node_modules/`、`web/.next/`：宿主机前端构建缓存；
- `.venv/`：无用（Python 3.6）；
- `deploy/web-build.log` 等日志。

**不要删除** `data/`、`deploy/`、`/root/deeptutor-vault/team`、`/root/team-docs`、
nginx 配置和证书，直到新镜像验证通过并完成一次完整备份。

分阶段清理、备份、回滚和长期运维见
[SERVER_CLEANUP_PLAN.md](SERVER_CLEANUP_PLAN.md)。

## 5. 验证清单

- [ ] 登录页正常，`zhangsan` 可登录；
- [ ] 题库能看到历史导入的练习与题图；
- [ ] PDF/DOCX 导入能预览、提交、显示题图；
- [ ] 学习记录侧栏按导入文件聚合，数量徽标正确；
- [ ] 视频讲解首次运行不再因 npm 缓存 EACCES 失败；
- [ ] 成员选择管理员共享书籍时能读到内容；
- [ ] 知识库检索仍能命中 `/vaults/team`；
- [ ] `docker compose ps` healthy，重启后数据仍在。

## 6. 回滚

保留旧镜像 tag 与 compose 备份：

```bash
docker tag ghcr.io/hkuds/deeptutor:latest deeptutor:rollback
cp /root/DeepTutor/deploy/compose.yml /root/DeepTutor/deploy/compose.yml.bak
```

新镜像异常时把 `image:` 改回旧 tag，恢复源码挂载方式，`docker compose up -d --force-recreate`。
