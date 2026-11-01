# 发布：提交代码与推送镜像（本 Fork 的操作手册）

[Handbook](README.md) · [Fork handoff](FORK_MIGRATION.md) · [Windows setup](WINDOWS_SETUP.md)

面向本 Fork 维护者，记录一次完整发布要走的每一步：代码进 GitHub、生产镜像进
GHCR、服务器拉取上线。命令都以当前目录（仓库根）为起点。

- 仓库：<https://github.com/HWOLFeng/DeepTutor>（分支 `main`）
- 镜像：`ghcr.io/hwolfeng/deeptutor:latest`（包页面见文末）
- 凭据：git 推送要 PAT（`repo`）；GHCR 推送要 PAT 带 **`write:packages`**（classic
  token 勾 `repo` + `write:packages`，或 fine-grained 勾 *Packages: Read and write*）

## 0. 前提与约定

- **token 不写进仓库、不写进命令行参数。** 放在 `~/.gh-token`（`chmod 600`），
  需要时用 `--password-stdin` 或 `GIT_ASKPASS` 读取，避免出现在 shell 历史与进程参数里。
- **代理只服务镜像拉/推**，不用于构建、也不进镜像。Docker daemon 的代理配置在
  `/etc/systemd/system/docker.service.d/proxy.conf`（本机是 `127.0.0.1:7890`），
  只影响 daemon 与 registry 通信。构建本身靠 Dockerfile 里的国内源。
- 本文的“发布”指 `main` 上的一次提交 + 一次镜像推送；两者独立，顺序不强制。

## 1. 提交代码到 GitHub

```bash
git pull --rebase origin main          # 先把远端的新提交叠到本地上面
git status --short                     # 确认要提交的内容
git add -A
git commit -m "fix(scope): 一句话说明改了什么"   # Conventional Commits，仓库历史同风格
```

推送（token 从文件读，不出现在参数里）：

```bash
cat > /root/.gh-askpass <<'SH'
#!/bin/sh
case "$1" in
  Username*) echo "HWOLFeng" ;;
  Password*) cat /root/.gh-token ;;
esac
SH
chmod 700 /root/.gh-askpass
GIT_ASKPASS=/root/.gh-askpass GIT_TERMINAL_PROMPT=0 git push origin main
rm -f /root/.gh-askpass
```

确认：

```bash
git status -sb        # ## main...origin/main（没有 ahead/behind）
```

## 2. 构建生产镜像

```bash
docker build --progress=plain --target production \
  -t ghcr.io/hwolfeng/deeptutor:latest .
```

- **必须写 `--target production`。** Dockerfile 最后一个 stage 是 `development`
  （带 `web/node_modules`，约 6.9 GB）；`docker build .` 不写 target 构建的就是它。
  生产镜像约 **3.1 GB**。
- **国内源已内置**（不需要代理）：apt/aliyun、pip/aliyun、npm/npmmirror、
  rustup/USTC。在其他地区构建时覆盖：

  ```bash
  docker build --target production \
    --build-arg APT_MIRROR=deb.debian.org \
    --build-arg PIP_INDEX_URL=https://pypi.org/simple \
    --build-arg NPM_REGISTRY=https://registry.npmjs.org \
    --build-arg RUSTUP_DIST_SERVER=https://static.rust-lang.org \
    --build-arg RUSTUP_UPDATE_ROOT=https://static.rust-lang.org/rustup \
    -t ghcr.io/hwolfeng/deeptutor:latest .
  ```

- 构建时可以把代理环境显式去掉，确认镜像不依赖代理：

  ```bash
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy docker build --target production ...
  ```

- 只改了标签/元数据时，层全部命中缓存（`CACHED`），几秒完成。

## 3. 验证镜像内容

```bash
docker run --rm --entrypoint sh ghcr.io/hwolfeng/deeptutor:latest -c "
  ls /app/deeptutor/services/practice/verify.py /app/deeptutor/services/practice/quality.py &&
  python -c 'import fastapi, PIL, pymupdf; print(\"deps ok\")' &&
  libreoffice --version | head -1"
```

顺手确认没有把代理烤进运行环境：

```bash
docker image inspect ghcr.io/hwolfeng/deeptutor:latest --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -i proxy || echo "no proxy env"
```

## 4. 推送镜像到 GHCR

```bash
docker login ghcr.io -u HWOLFeng --password-stdin < /root/.gh-token
docker push ghcr.io/hwolfeng/deeptutor:latest
```

- 层已存在时输出 `Layer already exists`，只上传变化的 config/manifest，很快。
- 推送成功会打印 `latest: digest: sha256:...`，记下它。

确认：

```bash
docker image inspect ghcr.io/hwolfeng/deeptutor:latest --format '{{index .RepoDigests 0}}'
docker manifest inspect ghcr.io/hwolfeng/deeptutor:latest | head -20
```

## 5. 在 GitHub 上确认

- 包页面：<https://github.com/users/HWOLFeng/packages/container/package/deeptutor>
- 仓库右侧的 **Packages** 区块只在镜像带
  `org.opencontainers.image.source="https://github.com/HWOLFeng/DeepTutor"` 且已推送后
  出现——这条 label 写在 Dockerfile 的 production stage。
- 查询链接状态（需要 token 带 `read:packages`）：

  ```bash
  curl -s -H "Authorization: Bearer $(cat /root/.gh-token)" \
    https://api.github.com/user/packages/container/deeptutor \
    | python -c "import json,sys; d=json.load(sys.stdin); print(d['visibility'], (d.get('repository') or {}).get('full_name'))"
  ```

## 6. 服务器拉取与部署

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u HWOLFeng --password-stdin
docker compose pull deeptutor
docker compose up -d deeptutor
docker logs --tail 30 deeptutor
```

镜像默认是 **private**，服务器必须登录 GHCR；也可以把包改成 public（包页面 →
*Package settings* → *Change visibility*），此时拉取免登录。

## 7. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 推送的镜像 6.9 GB | 构建时没写 `--target production` | 加 target 重新构建 |
| `permission_denied: The token provided does not match expected scopes` | PAT 没有 `write:packages` | 换带包权限的 token，重新 `docker login` |
| GitHub 上看不到镜像 | 包挂在**个人账号**的 Packages 下，不在仓库页面；未链接仓库时仓库侧栏不显示 | 用文末 URL 打开；或加 OCI source label 后重建推送；或在包设置里 *Connect repository* |
| 服务器 `docker pull` 403/未授权 | 包是 private 且服务器没登录 | 服务器 `docker login ghcr.io`，或把包改 public |
| 构建卡在 rustup / 下载很慢 | `static.rust-lang.org` 在国内不可达 | 已默认走 USTC 镜像；非国内环境用 build-arg 覆盖 |
| 构建 npm/pip 很慢 | 未走国内源 | 默认已走；检查 `--build-arg` 是否被覆盖成境外源 |
| 推送速度慢 | 走直连 | daemon 代理（`127.0.0.1:7890`）只影响 registry 通信，确认它可用 |

## 8. 本次发布记录（2026-10-31）

| 项 | 值 |
| --- | --- |
| 代码 | `731ed563` 等提交已推到 `origin/main` |
| 镜像 | `ghcr.io/hwolfeng/deeptutor:latest`，3.14 GB |
| digest | `sha256:0421e651edb6ed3721eafd23f17f1ecf18aa4f88ba3c645bd858a49b3ba67908` |
| 可见性 | public |
| 仓库链接 | `HWOLFeng/DeepTutor`（由 OCI source label 建立） |
