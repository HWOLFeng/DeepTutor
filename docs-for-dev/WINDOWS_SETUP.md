# Windows 上的开发与部署环境

本文是这份 Fork 在 Windows 主机上的操作手册：用 **WSL2 + Ubuntu + Docker Engine**
搭建与生产一致的开发环境，再从 Windows 侧构建镜像、部署回 Linux 服务器。
镜像构建与服务器侧的交接细节见 [FORK_MIGRATION.md](FORK_MIGRATION.md)，本文只补
Windows 侧缺的那一段。

标记约定：

- **✅ 已验证**：在本机按文中命令跑通，并记录了证据。
- **⏳ 待验证**：按设计应该成立，但尚未在本机执行。
- **⚠️ 坑**：踩过或极易踩到，附现象与处理。

## 最短路径（TL;DR）

只有 §3.1 需要管理员权限和一次重启，其余全部在普通终端 / Ubuntu 里完成：

```powershell
# 管理员 PowerShell，一次，然后重启
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
wsl.exe --update
wsl.exe --set-default-version 2
wsl.exe --install -d Ubuntu-24.04 --no-launch
```

重启后：确认 `wsl --status` 不再报虚拟化 → §3.2（systemd + mirrored 代理 + apt 源）
→ §3.3（默认用户 root、clone 到 `/root/DeepTutor`）→ §3.4（Docker Engine）
→ §3.5（`up -d --build`）→ `curl http://127.0.0.1:8001/health/ready`。

本机当前进度：§3.1–§3.4 已跑通（见 §7），§3.5 的镜像构建尚未执行。

## 0. 为什么不用 Windows 原生虚拟环境

仓库本身对 Windows 有支持（`sys.platform == "win32"` 分支遍布 parsing、sandbox、
launcher 等模块，CI 有 `windows-latest` 的 import 检查），但原生 Windows 不是主
验证平台，差异集中在四处：

| 能力 | Linux（生产/CI） | Windows 原生 |
| --- | --- | --- |
| 全量测试 | [tests.yml](../.github/workflows/tests.yml) 全部 `ubuntu-24.04` | 只有 import 检查 + MinerU 进程用例 |
| `exec` 沙箱 | `bwrap` 系统级隔离；compose 另有 `sandbox-runner` sidecar | 仅受限子进程（应用级），无 sidecar |
| 内存回收 / RSS 探针 | 可用（[memory_probe.py](../deeptutor/runtime/memory_probe.py)） | 不可用 |
| Office 预览 / math_animator | 镜像内置 LibreOffice、cairo、LaTeX、ffmpeg | 需自行安装 `soffice`、GTK/MSYS2、LaTeX |

结论：**改了 parsing、practice 导入、沙箱、契约生成或要跑 pytest 时，必须在
Linux 里验证**。只改 `web/` 里的 TSX、i18n、样式时，Windows 原生也够用（见 §4）。

## 1. 本机基线（2026-10-31 实测）

| 项 | 值 |
| --- | --- |
| 系统 | Windows 11 25H2（build 26200.9711，注册表 `ProductName` 仍显示 Windows 10） |
| 账户 | `wolf\hwolf`，在 `BUILTIN\Administrators` 组内，但日常进程**未提权** |
| Python | 3.12.3（`C:\Users\hwolf\AppData\Local\Programs\Python\Python312`） |
| Docker / Node / WSL 发行版 | 均未安装（`wsl.exe` 只是未启用功能的存根） |
| 代理 | Clash 类工具监听 `127.0.0.1:7890`，`git config --global http.proxy` 已指向它 |
| 仓库位置 | `C:\Users\hwolf\Desktop\project\DeepTutor`（fork `HWOLFeng/DeepTutor`，分支 `main`） |

⚠️ **代理端口和账户名会变**，换机器时重跑 §2 的检查命令，不要照抄 `hwolf` 路径。

## 2. 开工前的检查（无需管理员）

```powershell
# 系统版本与构建号
(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion') |
    Select-Object ProductName, DisplayVersion, CurrentBuild, UBR

# 是否已启用 WSL / 是否已有发行版
wsl --status; wsl --list --verbose

# 代理是否在监听（端口以你自己的客户端为准）
Test-NetConnection 127.0.0.1 -Port 7890 -InformationLevel Quiet

# 仓库状态
git -C C:\Users\hwolf\Desktop\project\DeepTutor status --short
```

`wsl --status` 输出"未安装用于 Linux 的 Windows 子系统"时，必须走 §3.1 的提权步骤：
**启用 WSL 需要管理员权限并重启**，普通权限的进程无法代替。

## 3. 方案 A（推荐）：WSL2 + Ubuntu + Docker Engine

选它的原因：源码在 ext4 上（inotify 可靠、`next dev` 不慢），容器与生产同构，
`exec` 沙箱与 `sandbox-runner` 都能跑，宿主不用装 Python/Node/GTK。

### 3.1 一次性提权启用 WSL2（需重启）

⚠️ **不要只跑 `wsl --install`。** 本机实测（2026-10-31）：非提权执行
`wsl --install -d Ubuntu-24.04` **看起来成功**——WSL 应用确实装上了
（`wsl --version` → 3.0.3.0），但两个可选功能没有启用，`wsl --status` 仍然报
「WSL2 无法启动，因为此计算机上未启用虚拟化」，`wsl -l -v` 里没有任何发行版。
更坑的是它会打印「请求的操作成功，直到重新启动系统前更改将不会生效」，让人以为
重启就能好；实际重启两次也没变化。

在 **管理员 PowerShell**（Win+X → 终端(管理员)）里执行整段：

```powershell
dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
wsl.exe --update
wsl.exe --set-default-version 2
wsl.exe --install -d Ubuntu-24.04 --no-launch
```

然后**重启**。重启后自检：

```powershell
wsl --status           # 期望只显示「默认版本: 2」，不再出现「未启用虚拟化」
wsl -l -v              # 期望出现 Ubuntu-24.04
```

**`State : Enable Pending` 卡住不生效时**（本机实测：连同两次重启都还是这个状态）：
功能其实已经提交，只是那次"重启"没完成服务栈收尾。Windows 的**快速启动**
（hybrid shutdown，`控制面板 → 电源选项 → 选择电源按钮的功能 → 启用快速启动`）
会让「关机再开机」只恢复休眠镜像，不执行 DISM 的收尾事务。判定与修法：

```powershell
powercfg /h off            # 关闭快速启动/休眠（管理员）
shutdown /r /f /t 0        # 强制完全重启，不要用「关机→开机」
```

重启后复检 `dism /english /online /get-featureinfo /featurename:VirtualMachinePlatform`，
`State` 应为 `Enabled`（不再是 `Enable Pending`）。仍然是 `Enable Pending` 说明 CBS
事务卡死，依次尝试 `dism /online /cleanup-image /restorehealth`、完成所有 Windows
Update 后再重启。

常见误判：

- `wsl --version` 能打印版本号 **不代表** WSL2 可用——那只是 WSL 应用自己。
- `HypervisorPresent=True`（CIM 查询）也不代表可用：VBS/内存完整性也会让它为真。
- 判定标准只有 `wsl --status` 不报「未启用虚拟化」 + `/proc/version` 里有
  `microsoft-standard-WSL2`。
- 只有 `wsl --status` 明确说固件虚拟化关闭时，才需要进 BIOS 打开 AMD SVM / Intel VT-x。

### 3.2 基础配置：systemd、网络、代理

先确认 systemd。Ubuntu 24.04 的 WSL 镜像通常已带 `[boot] systemd=true`（本机实测
开箱即是），只需验证：

```bash
cat /etc/wsl.conf                 # 期望有 [boot] systemd=true
systemctl is-system-running       # running 或 degraded 都可用
```

**网络走 mirrored + autoProxy，不要手工指 `HOST_IP`。** 本机实测：NAT 模式下 WSL
访问不到 Windows 的 `127.0.0.1:7890`（Clash 没开「允许局域网连接」），启动时还会警告
「检测到 localhost 代理配置，但未镜像到 WSL」。在 `%UserProfile%\.wslconfig` 写：

```ini
[wsl2]
networkingMode=mirrored
dnsTunneling=true
autoProxy=true
```

`wsl --shutdown` 后重进，WSL 自动注入代理环境变量，且 `127.0.0.1:7890` 在 WSL 内直达：

```bash
echo $HTTPS_PROXY                                            # http://127.0.0.1:7890
curl -sS -o /dev/null -w '%{http_code}\n' https://github.com # 200
```

apt 走国内源（对 apt 来说代理是额外一跳）：

```bash
sed -i 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g; \
        s|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  /etc/apt/sources.list.d/ubuntu.sources
apt-get update -qq
```

⚠️ 本机实测：`https://github.com` 走代理 200，但 `https://pypi.org/simple/` 走同一代理
仍失败（IPv6 优先、无法回退）。装 Python 包时把 index 指到国内源：

```bash
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' > ~/.pip/pip.conf
```

⚠️ mirrored 模式能规避「宿主访问不到容器端口」，但不同 WSL 版本仍有兼容问题；
出现容器起来了却打不开 `http://127.0.0.1:3782` 时，先 `docker compose ps` 看端口映射，
再考虑临时退回 NAT + 打开 Clash 的「允许局域网连接」。

### 3.3 默认用户与仓库位置：以 root 为准

本 Fork 的生产环境是「服务器 `/root/DeepTutor` + root 身份」，本地照抄这个形状，
免得路径/属主两套心智模型：

```bash
# /etc/wsl.conf
[boot]
systemd=true

[user]
default=root
```

`wsl --shutdown` 后不带 `-u` 进入即为 root。仓库：

```bash
apt-get install -y git curl ca-certificates
git clone https://github.com/HWOLFeng/DeepTutor.git /root/DeepTutor
cd /root/DeepTutor && git switch main
```

⚠️ 保留非 root 日常账号（`useradd -m wolf && usermod -aG sudo,docker wolf`）时，
`/root` 是 `700`，`wolf` **读不到** `/root/DeepTutor`。要么继续 `sudo -i` 操作，
要么把 `default=` 改回 `wolf` 并把仓库迁到 `/home/wolf/DeepTutor`——只能选一种，
混用会出现「同一个仓库两个属主 + 时不时 Permission denied」。

Windows 侧用 VS Code 的 WSL 扩展打开 `\\wsl$\Ubuntu-24.04\root\DeepTutor`。

### 3.4 安装 Docker Engine（不是 Docker Desktop）

apt 源用阿里云镜像（国内直连即可，不必经代理）：

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -qq
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

本机实测版本：**Docker 29.9.2 / Compose v5.6.1**。

拉镜像必须走代理，否则 `docker.io`、`ghcr.io` 拉不动：

```bash
mkdir -p /etc/systemd/system/docker.service.d
cat > /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://127.0.0.1:7890"
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1,::1"
EOF
systemctl daemon-reload && systemctl enable --now docker
docker run --rm hello-world      # 冒烟
```

⚠️ 这里能直接写 `127.0.0.1`，完全依赖 §3.2 的 mirrored 模式；NAT 模式下必须换成
Windows 的局域网 IP，并打开 Clash 的「允许局域网连接」。

⚠️ **已验证（2026-10-31）**：daemon 代理只覆盖镜像拉取，`docker build` 里跑 `npm ci` /
`pip install` 是另一套网络栈。`--build-arg HTTP_PROXY=http://127.0.0.1:7890` 配合
`--network host` 对 `npm ci` **无效**（连续两次 `ECONNRESET`，67s / 96s）；去掉代理直连
`registry.npmjs.org` 则 `ETIMEDOUT`（386s）。可行做法是**不注入代理、改用国内镜像源**
（apt→aliyun、npm→npmmirror、pip→清华、rust→rsproxy，并给 apt 加 `Acquire::ForceIPv4`），
完整配方与耗时见 §8 第 1 节的「构建配方」。

### 3.5 拉起开发栈

```bash
cd /root/DeepTutor
python3 scripts/docker_compose.py -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

`scripts/docker_compose.py` 只用标准库，作用是把 `data/user/settings/system.json`
里的端口渲染成 compose 环境变量；首次运行会自动创建 `data/`。

首次构建会走到 `development` 阶段（= `production` + 前端全量 `npm ci` + Python 依赖，
含 Rust 工具链阶段），耗时约十几到二十分钟、磁盘峰值 3–5 GB，属正常。

验证：

```bash
docker compose ps
curl -sf http://127.0.0.1:8001/health/ready && echo OK
docker compose logs -f deeptutor      # 后端 uvicorn + 前端 next dev 都在这一份日志里
```

浏览器打开 `http://127.0.0.1:3782`（端口以 `system.json` 为准）。源码目录在
[docker-compose.dev.yml](docker-compose.dev.yml) 里以只读方式挂进容器，改
`deeptutor/` 或 `web/` 会触发热重载，无需重建镜像。

⚠️ `web/` 只有列在 dev override 里的子目录会热重载；改 `package.json`、
`next.config.js`、`tsconfig.json` 之类需要 `up -d --build` 重建。

### 3.6 跑检查

```bash
docker compose exec deeptutor python -m pytest tests/services/practice tests/api/test_practice.py -q
docker compose exec deeptutor python -m ruff check .
docker compose exec -w /app/web deeptutor npm run typecheck
```

⚠️ 容器内 `python` 是镜像自带解释器，不是宿主 venv；不要 `docker exec ... pip install`
装东西——容器重建即丢。可选依赖请按 [docker-compose.yml](docker-compose.yml) 顶部
注释用 `DEEPTUTOR_EXTRAS` / `DEEPTUTOR_APT_PACKAGES` 声明。

## 4. 方案 B：Windows 原生（没有管理员权限时）

只建议用于「改前端 + 看效果」，或临时读代码。Python 3.12 已在 `PATH` 上。

```powershell
cd C:\Users\hwolf\Desktop\project\DeepTutor
$env:HTTPS_PROXY='http://127.0.0.1:7890'; $env:HTTP_PROXY=$env:HTTPS_PROXY   # pip 走代理
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -r requirements/full.txt
python -m pip install -e . --no-deps

# Node 22 LTS 用免安装 zip（不需要管理员），解压后加进 PATH
# https://npmmirror.com/mirrors/node/v22.x.x/node-v22.x.x-win-x64.zip
cd web; npm ci --legacy-peer-deps; cd ..
```

启动：

```powershell
$env:DEEPTUTOR_HOME='C:\Users\hwolf\Desktop\project\DeepTutor'
deeptutor init --non-interactive
deeptutor serve --host 127.0.0.1 --port 8001 --reload      # 终端 1
cd web; $env:DEEPTUTOR_API_BASE_URL='http://127.0.0.1:8001'; npm run dev   # 终端 2
```

⚠️ 已知降级：`exec` 只有受限子进程（应用级隔离）、内存回收不可用、Office 预览需要
自行安装 LibreOffice、math_animator 需要 GTK/cairo/LaTeX。这些功能在 Windows 原生
下的失败**不代表**会在服务器上失败，反之亦然——需要在 Linux 里复核。

## 5. 从 Windows 构建镜像并部署回服务器

在 WSL 的 Ubuntu 里做完 §3.4 之后，构建比 Docker Desktop 快（无 9p 层）：

```bash
cd ~/DeepTutor
git status --short           # 确认没有未提交/未测试的改动
docker buildx build --platform linux/amd64 -t ghcr.io/hwolfeng/deeptutor:latest --push .
```

不适合推 GHCR 时导出 tar，再传到服务器：

```bash
docker build --platform linux/amd64 -t deeptutor-fork:latest .
docker save deeptutor-fork:latest -o deeptutor-fork.tar
scp deeptutor-fork.tar root@<server>:/root/
# 服务器：docker load -i /root/deeptutor-fork.tar && docker compose up -d
```

构建后确认改动确实进了镜像（服务器最常踩的坑是拉到了旧镜像）：

```bash
docker run --rm --entrypoint sh ghcr.io/hwolfeng/deeptutor:latest -c \
  "grep -n question_images /app/deeptutor/services/session/sqlite_store.py | head"
```

服务器侧的数据备份、`data/` 保留范围和回滚步骤见 [FORK_MIGRATION.md](FORK_MIGRATION.md) §3。

## 6. 常见坑

1. **`wsl --install` 非提权会「假成功」**：WSL 应用装上、`wsl --version` 有版本号，
   但可选功能没启用，`wsl --status` 报「未启用虚拟化」，`wsl -l -v` 无发行版，
   且提示「重启后生效」却重启无效。判定与修法见 §3.1。
2. **TLS 握手失败 `SEC_E_NO_CREDENTIALS`**：低完整性（受限）进程访问不到用户的证书
   凭据，任何 `curl`/`Invoke-WebRequest` 都会失败。用普通终端或 WSL 里的 `curl` 复现，
   不要据此判断「机器断网」。
3. **`wsl.exe` 输出是 UTF-16LE**：在 PowerShell 里直接 `| Out-String` 会得到乱码。
   用 `cmd /c "wsl.exe ... > out.txt 2>&1"` 落盘后 `Get-Content out.txt -Encoding Unicode`。
4. **中文系统上 `findstr "State"` 筛出空行**：`dism` / `systeminfo` 的字段名是本地化的
   （`状态 : 已禁用`），按英文关键字过滤必然匹配不到，看起来像「命令没输出、功能不存在」。
   加 `/english`（`dism /english /online /get-featureinfo ...`）或先 `chcp 65001`，
   再看完整输出——不要用 `findstr "State"` 的结果下结论。
5. **`systeminfo` 显示「已检测到虚拟机监控程序。将不显示 Hyper-V 所需的功能」**：
   说明已有一个 hypervisor 在跑（VBS/内存完整性，或宿主本身就是虚拟机），于是
   `systeminfo` 不再打印 VT-x/SLAT 那几行。要那几行就查
   `Get-CimInstance Win32_Processor | Select VMMonitorModeExtensions,SecondLevelAddressTranslationExtensions`，
   并结合 `wsl --status` 判断，不要单独采信任何一项。
6. **`docker pull` 卡住**：先确认 daemon 代理（§3.4）与 Clash 的局域网许可；改用
   `mirror` 源只是备选。
7. **热重载不生效**：检查是不是把仓库放在了 `/mnt/c`；再确认改的文件在
   [docker-compose.dev.yml](docker-compose.dev.yml) 的挂载清单里。
8. **CRLF**：仓库的 [.gitattributes](../.gitattributes) 已强制 `*.py`/`*.sh`/`Dockerfile`
   等为 LF，别用会改写行尾的编辑器保存 Dockerfile 与 shell 脚本。建议
   `git config --global core.autocrlf false`。
9. **端口被占**：容器端口取自 `data/user/settings/system.json`；改端口后需要重新
   `up -d`，不要只改宿主的映射。
10. **别把 `data/` 提交进 Git**：账号、密钥、聊天与练习记录都在里面（见
    [FORK_MIGRATION.md](FORK_MIGRATION.md) §1.2）。

## 7. 验证记录

| 日期 | 步骤 | 结果 |
| --- | --- | --- |
| 2026-10-31 | 基线检查（§1/§2） | ✅ 无 Docker/Node/WSL 发行版；代理 7890 在监听 |
| 2026-10-31 | `wsl --install`（未提权） | ❌ 退出码 1；且见下一行 |
| 2026-10-31 | 未提权 `wsl --install -d Ubuntu-24.04 --no-launch` | ⚠️ **假成功**：WSL 应用 3.0.3.0 装上，功能未启用；重启 2 次后 `wsl --status` 仍报「未启用虚拟化」、`wsl -l -v` 无发行版 |
| 2026-10-31 | 提权 `dism` 启用两功能 | ⚠️ 落到 `State : Enable Pending`；快速启动导致重启不生效，`powercfg /h off` + `shutdown /r /f /t 0` 后变 `Enabled` |
| 2026-10-31 | Ubuntu-24.04 发行版安装 | ✅ Ubuntu 24.04.5，kernel `6.18.54.1-microsoft-standard-WSL2`，16 vCPU / 13.8 GB / 1007 GB 可用 |
| 2026-10-31 | `.wslconfig` mirrored + autoProxy | ✅ 代理 `127.0.0.1:7890` 在 WSL 内直达；`github.com` 200（`pypi.org` 仍失败，改用清华源） |
| 2026-10-31 | 默认用户切到 root | ✅ `id` → `uid=0(root)`；`/root/DeepTutor` 可读写 |
| 2026-10-31 | Docker Engine 安装 | ✅ Docker 29.9.2 / Compose v5.6.1；`docker run --rm hello-world` 成功（经 daemon 代理） |
| 2026-10-31 | 仓库 clone 到 `/root/DeepTutor` | ✅ HEAD `93957e5c`，含未提交的 3 个文档改动 |
| 2026-10-31 | 容器由用户手工构建 | ✅ 用户完成（形态未记录，见 §8 第 0 步） |
| 2026-10-31 | §0 基准复核 | ✅ HEAD `93957e5c` + 3 个文档改动；`docker ps -a` 空（无 app 容器），daemon `29.9.2 containers=0` |
| 2026-10-31 | dev 镜像构建（国内镜像源） | ✅ `deeptutor-deeptutor:latest`（`--target development`）6.91 GB，全镜像源重建约 20 分钟。**报错原文**：注入 `--build-arg HTTP_PROXY/HTTPS_PROXY` 时 `npm ci` 两次 `npm error code ECONNRESET`（67s、96s）；去掉代理直连 `npm error code ETIMEDOUT … errno -110`（386s）。改为 apt→aliyun(+`Acquire::ForceIPv4`)、npm→npmmirror、pip→清华、rust→rsproxy 后 `added 997 packages in 2m` 通过 |
| 2026-10-31 | dev 栈构建 + `/health/ready` | ✅ 4 容器 `healthy`（`deeptutor`、`deeptutor-redis`、`deeptutor-sandbox-runner`、`pocketbase`）；`curl /health/ready` → 200；前端 `3782` 返回 `307`（`proxy.ts` 语言重定向），`GET /en` 编译 5.6s 后 404 |
| 2026-10-31 | 测试基线（Python / Web） | ⚠️ 见 §8 第 3 节：practice 36 passed；ruff `check` 3 errors、`format --check` 5 files；typecheck exit 0；`test:node` 1302 pass / 4 fail（容器形态假失败）；`test:unit` 995/995（须 `NODE_ENV=test`）；`i18n:check` exit 1（18 处 UI 字面量） |
| 2026-10-31 | 全量 pytest 基线 | ⚠️ 两次：A 纯净镜像 `89 failed / 10538 passed / 111 skipped`（12:58，其中约 55 条只是镜像缺 `Dockerfile`/`.github`/`docker-compose*.yml` 等文件）；B 补齐这些文件后 `32 failed / 10595 passed / 111 skipped`（9:26）。剩余失败分类见 §8 第 3 节 |
| 2026-10-31 | provider / embedding 配置 | ✅ `config apply` 写入 `deepseek/deepseek-flash` + `aliyun/text-embedding-v4 (1024)`；`--check` 与 apply 均 `ok`；`doctor --format json` 四项全 pass，`doctor --online` → `The model returned a response`；应用层 embedding `2 vectors dim 1024`。⚠️ 原写 `deepseek-v4.1-flash` 不存在（账号仅 `deepseek-flash`/`deepseek-v4-pro`），已改 |

> 每一步跑通后把「⏳」改成「✅」，并把关键输出（端口、耗时、报错原文）补进本表；
> 否则这份文档会退化成一份没人验证过的清单。

## 8. 交接：容器内开发的未完成事项

**当前形态**：DSH 通过插件直接进入 WSL 工作区 `/root/DeepTutor`（ext4，默认用户 `root`），
不需要「SSH 进容器」。2026-10-31 已用 compose 起 dev 栈：**app 容器名 `deeptutor`**，
同栈还有 `deeptutor-redis`、`pocketbase`、`deeptutor-sandbox-runner`，四个都 `healthy`。
端口 `8001`（后端）/ `3782`（前端）按 `data/user/settings/system.json` 渲染。

⚠️ **实测纠正**：dev override 只把 `deeptutor/`、`deeptutor_cli/`、`scripts/`、`web/*` 挂进
`/app`，且全部是 **`:ro`**；只有 `data/` 与 `data/user/workspace` 是 `:rw`。所以
`/app` **不是**仓库全量视图——容器里没有 `.git`、`tests/`、`README.md`、`Dockerfile`，
`/app` 下也不能写。原先「`/root/DeepTutor` 与 `/app` 两个视角 `git status` 必须一致」的说法
在本形态下不成立：**源码真值仍以 `/root/DeepTutor` 为准**，容器只负责跑服务、只读源码。

**上次停在哪**：WSL2 + Ubuntu 24.04.5 + Docker 29.9.2 已就绪，仓库 HEAD `93957e5c`，
3 个文档改动未提交；app 容器形态未记录。→ 本次已补齐（见下表与第 1 节）。

**实测值（2026-10-31 复核，DSH 会话）**

| 项 | 实际值 |
| --- | --- |
| 工作区 | `/root/DeepTutor`（WSL2 ext4，默认用户 `root`），`git` 分支 `main` |
| `git log -1` | `93957e5c chore(docker): include license and notices in the image` |
| `git status --short` | `M docs-for-dev/FORK_MIGRATION.md` / `M docs-for-dev/README.md` / `?? docs-for-dev/WINDOWS_SETUP.md` —— 与「基准 3 个文档改动」一致 |
| 复核前 app 容器 | **无**：`docker ps` 与 `docker ps -a` 均为空；daemon 29.9.2 可达（`containers=0 running=0`） |
| 复核后 app 容器 | `deeptutor`（镜像 `deeptutor-deeptutor:latest`，`development` 阶段），`Up (healthy)`，`0.0.0.0:8001->8001`、`0.0.0.0:3782->3782` |
| 本机镜像 | 复核前只有 `node:22-slim`、`hello-world`；复核后新增 `deeptutor-deeptutor:latest`（6.91 GB）、`deeptutor-sandbox-runner:latest`、`redis:7.4-alpine`、`ghcr.io/muchobien/pocketbase:latest` |
| 执行面 | 统一 `docker exec deeptutor …`；宿主没有 Python 依赖、没有 `node`（宿主 `python3` 仅 3.12.3，只能跑 `scripts/docker_compose.py`） |
| 容器内版本 | `Python 3.11.18` / `node v22.23.4` / `npm 10.9.9`；`deeptutor` CLI 未装脚本入口，用 `python -m deeptutor_cli.main` |

所有 `[ ]` 都是给新会话的作业；完成后就地改成 `[x]` 并补一行证据。

### 0. 确认工作区与容器的对应关系

- [x] `git -C /root/DeepTutor log -1 --format='%h %s' && git -C /root/DeepTutor status --short`
      —— 基准应是 `93957e5c` + 3 个文档改动；对不上就先查是不是另一个副本
      - 证据（2026-10-31）：`93957e5c chore(docker): include license and notices in the image`；
        `status --short` → ` M docs-for-dev/FORK_MIGRATION.md` / ` M docs-for-dev/README.md` /
        `?? docs-for-dev/WINDOWS_SETUP.md`。与基准一致，无需另找副本。
- [x] `docker ps --format '{{.Names}}\t{{.Ports}}\t{{.Image}}'` —— 有没有 app 容器在跑。
      **没有**说明当前只在 WSL 里开发，第 1 步直接用宿主命令起服务
      - 证据（2026-10-31）：`docker ps` / `docker ps -a` 均为**空输出**；
        `docker info` → `29.9.2 containers=0 running=0`（daemon 正常，确实没有容器）。
- [x] 有容器时：`docker inspect deeptutor --format '{{json .Mounts}}' | jq` —— 确认
      `/root/DeepTutor` 以**读写**挂到 `/app`（只读 / 没挂 / 挂到别处，都要先修，见"已知风险 2"）
      - 证据（2026-10-31，建栈后复核）：`docker inspect deeptutor --format '{{range .Mounts}}…'`
        → 18 条 bind mount，源码子树全部 `rw=false`：`deeptutor→/app/deeptutor`、
        `deeptutor_cli→/app/deeptutor_cli`、`scripts→/app/scripts`、`web/{app,components,context,features,hooks,i18n,lib,locales,public}→/app/web/*`；
        `data→/app/data`、`data/user`、`data/memory`、`data/knowledge_bases`、
        `data/user/workspace→/workspace` 为 `rw=true`。**结论**：不是「整仓读写挂 `/app`」，
        是 dev override 的只读子树挂载，见本节开头的「实测纠正」。
- [x] 有容器时：`docker exec deeptutor bash -lc 'python -V; node -v; git -C /app log -1 --format=%h'`
      —— 头一个 commit 必须与第一条对得上，否则容器里跑的不是工作区这份代码
      - 证据（2026-10-31）：`Python 3.11.18`、`node v22.23.4`；
        `git -C /app` → `fatal: not a git repository`（`.git` 没挂进容器，故容器内无法核对
        commit）。等价核对：`/app/deeptutor` 是宿主 `/root/DeepTutor/deeptutor` 的 bind mount，
        内容即 HEAD `93957e5c` 的只读视图。
- [x] 把实际值补进本节，替换所有 `<容器>` 占位符
      - 证据（2026-10-31）：本节顶部已补「实测值」表；本节的 `<容器>` 统一替换为
        `deeptutor`（[docker-compose.yml](../docker-compose.yml) 的 `container_name`），
        剩下 1 处在第 1 节，随该节一起替换。

### 1. 让服务真正跑起来

先定"执行面"，别一半在宿主一半在容器：**本机取「容器」这一侧**——宿主没有 Python
依赖、没有 `node`，只有 `docker`。

- [x] 有 app 容器 → 所有 `python` / `pytest` / `npm` 命令统一加 `docker exec deeptutor` 前缀
      - 证据（2026-10-31）：本节的验证、§3 的 pytest/ruff/npm 全部以 `docker exec deeptutor …`
        执行；容器名 `deeptutor`（compose `container_name`）。
- [x] 没有容器、也不打算在 WSL 里装 Python 依赖 → 用 `python3 scripts/docker_compose.py
      -f docker-compose.yml -f docker-compose.dev.yml up -d` 把服务跑起来，WSL 只负责编辑与 git
      - 证据（2026-10-31）：`up -d --no-build --no-deps deeptutor` 先起 app，再
        `up -d --no-build --no-deps redis pocketbase sandbox-runner` 补齐侧车
        （`--no-build` 因为镜像已按下面「构建配方」自建）。四个容器状态：
        `deeptutor / deeptutor-redis / deeptutor-sandbox-runner / pocketbase` 全 `(healthy)`。
- [x] 若容器只是 `sleep infinity` 常驻：在容器内起后端 …（命令与 Dockerfile 的 `development`
      阶段一致）
      - 证据（2026-10-31）：**不适用**——容器由 image `ENTRYPOINT /app/entrypoint.sh` +
        supervisord 常驻，`docker logs` 可见 `spawned: 'backend'`/`'frontend'`，
        后端是 `uvicorn … --reload`，前端是 `node scripts/dev.mjs`，无需手工起。
- [x] 若改用 compose + [docker-compose.dev.yml](docker-compose.dev.yml)：注意它把
      `deeptutor/`、`web/*` 挂成 **`:ro`**，改代码不会落盘——需要额外的读写覆盖，否则
      「改了没反应」会被误判成热重载坏了
      - 证据（2026-10-31）：`docker inspect` 显示源码子树全部 `rw=false`（§0 已列）。
        **但热重载仍生效**：bind mount 的只读是容器侧写权限，宿主的写入照常出现在容器里，
        WatchFiles / Turbopack 都能看到（见下一条）。真正被 `:ro` 挡住的是「容器内写」：
        `npm run test:unit` 里 `copy-pdfjs-assets` 要写 `/app/web/public/pdfjs` 直接 `ENOENT`。
- [x] 验证热重载：改一行后端代码看 uvicorn 是否重启；改一个 `web/components/*.tsx` 看
      `next dev` 是否重新编译（这是判断「ext4 还是 9p」的唯一可靠办法）
      - 证据（2026-10-31）：宿主 `touch deeptutor/...` + `docker cp tests` 后，
        `docker logs` 出现 `WatchFiles detected changes in 'tests/…' … Reloading...` +
        `Started server process [248]`；前端侧 `GET /en` 触发
        `○ Compiling /_not-found/page ... GET /en 404 in 5.6s`，说明跑的是 `next dev` 而非预编译产物。
        ⚠️ 同一条日志里也踩到 watcher 崩溃（`WatchfilesRustInternalError … /app/data/system/user-mcp:
        Permission denied`），见「已知风险 5」。
- [x] `curl -sf http://127.0.0.1:8001/health/ready && echo OK` —— 后端就绪
      - 证据（2026-10-31）：`ready=200`；容器 healthcheck 也因此 `(healthy)`。
- [x] 浏览器打开 `http://127.0.0.1:3782` —— 前端可达（mirrored 网络下宿主的
      `127.0.0.1` 与 WSL 共享，打不开先看 §3.2 末尾的提示）
      - 证据（2026-10-31）：`curl -o /dev/null -w '%{http_code}' http://127.0.0.1:3782/`
        → `307`（`proxy.ts` 按语言重定向），`GET /en` → `404` 但在 `5.6s` 内完成编译，
        说明 Next dev 正在按需编译而不是返回静态 404。

**构建配方（2026-10-31，国内网络下必须这样）**

1. 直接用官方 [Dockerfile](../Dockerfile) 会卡在 `npm ci`：`--build-arg HTTP_PROXY=…`
   两次 `ECONNRESET`（67s / 96s），去掉代理直连则 `ETIMEDOUT`（386s）。
2. 可行做法：复制一份 Dockerfile 打四个补丁（本次用临时文件 `Dockerfile.npm-mirror` /
   `Dockerfile.runner.mirrors`，属一次性构建辅助，不入库）：
   - apt：`deb.debian.org` → `https://mirrors.aliyun.com`，并加
     `printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4`
     （容器有 IPv6 地址但连不通，不加会一个包卡十几秒）；
   - npm：`ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com`（`npm ci` 从超时变成 2 分钟）；
   - pip：`ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`；
   - rust：`RUSTUP_DIST_SERVER=https://rsproxy.cn` + `/root/.cargo/config.toml` 指向
     `sparse+https://rsproxy.cn/index/`。
3. 命令：`docker build --network host -f Dockerfile.npm-mirror --target development
   -t deeptutor-deeptutor:latest .`；runner：`-f Dockerfile.runner.mirrors -t
   deeptutor-sandbox-runner:latest .`。
4. 实测耗时：从 15:33 第一次尝试到 16:33 得到可用镜像，其中全镜像源那次重建约 20 分钟
   （npm ci 2 分钟、pip 约 9 分钟、apt 数分钟），产物 `deeptutor-deeptutor:latest` 6.91 GB。
5. `data/` 必须 `chown -R 1000:1000 data`：entrypoint 明确拒绝 `PUID=0`（"must be non-root"），
   容器内进程固定 uid 1000，宿主 root 拥有的 `data/` 会让它写不进 settings。

### 2. 运行时数据与配置

- [x] `DEEPTUTOR_HOME=/app deeptutor init --non-interactive`（容器内 `/app/data` 是否
      已有数据要按实际情况判断，**不要**覆盖既有账号/密钥）
      - 证据（2026-10-31）：镜像里没有 `deeptutor` 脚本入口，等价命令
        `python -m deeptutor_cli.main init --non-interactive` →
        `{"ok": true, "status": "initialized", "runtime_home": "/app"}`。
        跑之前先 `cp /app/data/user/settings/auth.json /tmp/auth.json.bak`；
        `/app/data/user/settings/` 里 `agents.yaml`/`auth.json`/`model_catalog.json` 等
        是 entrypoint 首启自动生成的，未被覆盖。
- [x] 配置 provider 与 model（key 走环境变量或设置页，**不要**贴进聊天或提交进仓库）
      - 证据（2026-10-31）：用 `config apply` 非交互写入，key 全程只经进程环境传递
        （`docker exec -e DT_LLM_KEY -e DT_EMB_KEY …`，命令行里只有变量名），
        setup 文件放在仓库外的 `/root/setup-dt.json`（mode 600）。
        `--check` → `{"ok": true, "status": "validated"}`；正式 apply →
        `{"ok": true, "status": "applied"}`。
        `config show`：`llm=deepseek/deepseek-flash @ https://api.deepseek.com`、
        `embedding=aliyun/text-embedding-v4 dimension=1024`，两者 `api_key: "***"`。
      - 模型名按实测改过：`GET https://api.deepseek.com/models` 只返回
        `deepseek-flash`、`deepseek-v4-pro`，原计划的 `deepseek-v4.1-flash` 不存在，
        经确认改用 `deepseek-flash`；embedding 侧 `text-embedding-v4` + `dimension=1024`
        直连 DashScope 实测 `dim=1024`。
      - 联调验证：`doctor --format json` 四项全 pass（`llm_config`/`llm_credentials`/
        `llm_endpoint`/`storage`）；`doctor --online` →
        `Provider response: The model returned a response`；应用层
        `get_embedding_client().embed(["ping","深蓝"])` → `binding: aliyun`、
        `model: text-embedding-v4`、`2 vectors`、`dim 1024`。
        重启容器后 `/health/ready` 仍 200。
      - 落盘：密钥写入 `data/user/settings/model_catalog.json`（`-rw-------`、属主 1000），
        该路径被 `.gitignore` 的 `data/` 覆盖（`git check-ignore` 命中），**未进仓库**。
- [x] `deeptutor module list` + `deeptutor doctor --format json`，记录模块 ready 状态
      - 证据（2026-10-31）：`python -m deeptutor_cli.main module list` → 25 个模块、
        0 个 disabled，21 个 `ready: true`；**4 个不 ready**：
        `parser-docling`（缺 `docling[xbrl]>=2.123.1`、`docling-slim[…]`）、
        `parser-liteparse`（缺 `liteparse>=2.14.2`）、`parser-markitdown`（缺 `markitdown[all]`）、
        `renderer-manim`（缺 `manim`、缺可执行 `ffmpeg`/`latex`）。
        `doctor --format json`（此时 provider 还没配）：`storage: pass
        (Writable: /app/data/user)`、`llm_endpoint: pass (https://api.openai.com)`，
        `llm_model`/`llm_credentials: fail`，`rag`/`online: skip`。
        —— 配好 provider 后已全部 pass，见本节上一条。
- [x] 确认 `data/` 是宿主目录挂进来的（`docker inspect` 里能看到），容器重建不丢数据
      - 证据（2026-10-31）：`docker inspect deeptutor` →
        `/root/DeepTutor/data → /app/data rw=true`、
        `/root/DeepTutor/data/user/workspace → /workspace rw=true`；
        宿主 `data/user/settings/interface.json` 等在容器内写入后宿主可见。

### 3. 测试基线（这是换到 Linux 的根本目的）

- [x] `python -m pytest tests/services/practice tests/api/test_practice.py -q` ——
      覆盖本 Fork 的 PDF/DOCX 题目导入与题图改动
      - 证据（2026-10-31）：**`tests/services/practice` 在本 checkout 里不存在**
        （`ERROR: file or directory not found`），实际对应文件是
        `tests/api/test_practice.py` + `tests/services/skill/test_practice_template_skill.py`。
        改跑这两个：`36 passed in 8.64s`。
- [x] `python -m pytest tests -q` 全量，记录**新增失败**与**跳过原因**（Redis、可选引擎、
      平台可执行文件），不要只报「全绿/全红」
      - 证据（2026-10-31，跑了两次）：
        **A. 镜像原样**（只 `docker cp tests` + `README.md`）→
        `89 failed, 10538 passed, 111 skipped … in 778.47s (12:58)`；
        **B. 补齐仓库文件后**（把 `.git`、`.github`、`docs-for-dev`、`packaging`、
        `Dockerfile`、`docker-compose*.yml`、`start_deeptutor.command`、`build_support.py`
        等 `tar` 进 `/app`，并删掉 `/tmp/deeptutor-environment-*.lock`）→
        `32 failed, 10595 passed, 111 skipped … in 566.87s (9:26)`。
        即 A 的 89 条里约 55 条只是「镜像里没有这些文件」（`FileNotFoundError:
        /app/Dockerfile`、`/app/.github/workflows/*`、`/app/docker-compose.yml`、
        `docs-for-dev/examples/...`）。
      - B 剩下的 32 条按成因分：
        **① 容器环境（非代码）**：entrypoint 导出 `DEEPTUTOR_IGNORE_PROCESS_ENV_OVERRIDES=1`，
        于是「进程环境覆盖设置」类测试全挂——`test_runtime_settings`、`test_launch_settings`、
        `test_openai_http_client`(5)、`test_disable_ssl_verify`(5)、
        `test_cloud_provider::test_ssl_connector`、`test_codex_disable_ssl_verify`、
        `test_multimodal`；`/tmp/deeptutor-environment-*.lock` 属主 1000 让 CLI 租约类报
        `PermissionError`（A 里 11 条，B 删锁后消失）；`test_data_volume` 报
        `DataVolumePermissionError (euid=1000)` 是「测试以 root 跑、目录属 1000」的错配。
        **② 仓库既有问题（可单独修）**：`test_router_loader::test_repository_index_matches_router_sources`
        （提示 `Run scripts/build_router_index.py`，路由索引已过期）、
        `test_docs_contract::test_handbook_links_resolve_and_index_reaches_every_guide`
        （`docs-for-dev/DEVELOPMENT.md: broken link ../CONTRIBUTING.md`）、
        i18n 的 de/uk/pl 各 1 处缺键 + `test_web_locale_covers_every_english_key`、
        `test_kb_eval_cli::test_eval_reports_an_invalid_dataset`（`case \n#1` 换行转义）。
        **③ 未判定（需人工看）**：`test_office_artifact_execution`、
        `test_pptx_workspace_flow`（都是「依赖是否打进 pip/source/docker」的断言）、
        `test_skill_resolution_scoped`、`test_workspace_binding`、
        `test_mineru_process_lifecycle`(3)、进程树回收 2 条（`Owned child escaped cleanup`）。
      - `111 skipped` 主要是 Redis / 可选引擎 / 平台可执行文件缺失，未逐条展开。
      - ⚠️ 前提：全量是在「镜像 + 手工补文件」的临时容器里跑的；纯净镜像里直接
        `docker exec deeptutor pytest tests` 会先 `collection error` 中断（见已知风险 6）。
- [x] `python -m ruff check .` 与 `python -m ruff format --check .`
      - 证据（2026-10-31，ruff 0.17.3）：`ruff check .` → **3 errors**，全是 `I001`
        import 排序：`deeptutor/services/practice/document_import.py:15`、
        `healthcheck.py:1`、`scripts/build_modules.py:22`。
        `ruff format --check .` → **5 files would be reformatted**
        （`deeptutor/services/practice/document_import.py`、`deeptutor/services/session/sqlite_store.py`、
        `deeptutor/skills/builtin/practice-template/SKILL.md`、`tests/api/test_practice.py`、
        `tests/services/skill/test_practice_template_skill.py`）。
        这些都是 HEAD `93957e5c` 上的既有状态，非本次改动引入。
- [x] `cd /app/web && npm run typecheck && npm run test:node && npm run test:unit`
      - 证据（2026-10-31）：
        `npm run typecheck` → exit 0（无输出即通过）；
        `npm run test:node` → `# pass 1302 / # fail 4`，4 个都是**容器形态导致的假失败**：
        `git ls-files web` 报 `not a git repository`（镜像没 `.git`）×2、
        `copy-pdfjs-assets` 写 `/app/web/public/pdfjs` 报 `ENOENT`（`public` 是 `:ro` 挂载）、
        「container dev stage routes through the wrapper」找不到 `/app/Dockerfile`；
        `npm run test:unit` **必须带 `NODE_ENV=test`**：镜像 `ENV NODE_ENV=production`
        会让 React 19 生产构建缺 `act`，裸跑是 `866 failed / 970`；带 `NODE_ENV=test` 后
        **`Test Files 196 passed / Tests 995 passed`，exit 0**。
- [x] `npm run i18n:check`（本 Fork 改过 `web/locales/*/app.json`）
      - 证据（2026-10-31）：exit 1。`i18n:parity` OK；`i18n:audit` → en/zh 各
        `4407/4407 (100%)`，fr `4177/4407 used (94.8%)`、de/uk/pl 各 `4406/4407` 且各有
        1 个 `t()` 字面量缺条目，另有 **18 files with potential UI literals**
        （如 `app/(auth)/login/page.tsx` 的 `placeholder: "you@example.com"`、
        `UsageSettingsSection.tsx` 的 `"tokens"`）——是 i18n 债，不是环境问题。

### 4. 那些「只有 Linux 才能验」的能力

- [x] `exec` 沙箱：确认走的是 runner sidecar（`DEEPTUTOR_SANDBOX_RUNNER_URL`）还是容器内
      受限子进程；跑一次真实命令并记录隔离级别
      - 证据（2026-10-31）：容器 `env` 有 `DEEPTUTOR_SANDBOX_RUNNER_URL=http://sandbox-runner:8900`；
        `get_sandbox_service()` → backend `RunnerSidecarBackend`，`isolation_level()` → `system`。
        真实命令（`id; hostname; uname -sr; ls -la /workspace/outputs`）exit 0，
        输出 `uid=1000(runner) gid=1000(runner)`、`Linux 6.18.54.1-microsoft-standard-WSL2`，
        `/workspace/outputs` 属主 `runner` —— 确认是在 sidecar 里以非 root 跑的。
- [x] Office 预览：容器里 `which soffice`，用一份 docx/xlsx 触发预览
      - 证据（2026-10-31）：`which soffice` → `/usr/bin/soffice`（镜像装了
        libreoffice-writer/calc/impress + 中文字体）；用 python-docx 生成
        `/tmp/preview-smoke.docx` 跑 `soffice --headless --convert-to pdf` →
        `convert … as a Writer document -> /tmp/preview-smoke.pdf`，产物 18453 bytes
        （仅 `javaldx` 告警，不影响转换）。
- [x] `math_animator`：`which latex ffmpeg`；如需 Manim，`python scripts/install_extras.py math-animator`
      - 证据（2026-10-31）：`which latex ffmpeg pdflatex` → 全部 absent；
        `module list` 里 `renderer-manim` 报 `Missing package: manim>=0.19.0`、
        `Missing executable: ffmpeg`、`Missing executable: latex`。
        **未安装**（`math-animator` 属可选，装了要拉 Manim + LaTeX，非本步目标）；需要时按
        [Dockerfile](../Dockerfile) 的 `DEEPTUTOR_EXTRAS` / `DEEPTUTOR_APT_PACKAGES` 声明重建镜像。
- [x] `video_lesson`：Node 可用、npm 缓存目录可写（本 Fork 已修过这点，值得顺手回归）
      - 证据（2026-10-31）：`node -v` → `v22.23.4`，`npm 10.9.9`，
        `npm config get cache` → `/root/.npm`，在其下 `mkdir` 成功（可写）。

### 5. DSH 接入

- [x] 已通过插件进入 WSL 工作区 `/root/DeepTutor`（2026-10-31），**不需要**在容器里装
      `openssh-server` / 走 `rw_connect`
- [x] 验证往返：在本会话改一行、跑一次命令，确认改动体现在 `/root/DeepTutor` 的
      `git status` 里（而不是只落在某个缓存或快照中）
      - 证据（2026-10-31）：用 DSH 写文件工具新建 `/root/DeepTutor/.dsh-roundtrip-probe`
        （53 bytes），随后在同目录用 shell 读到同一内容、`stat` 显示
        `dev=2096 inode=722689 mtime=2026-10-31 16:51:35`（WSL ext4，不是 `/mnt/c`），
        `git status --short` 出现 `?? .dsh-roundtrip-probe`；删除后 status 恢复干净。
        本节所有 `[x]` 与证据行本身也是这样写回仓库的。
- [x] 如果实际要执行命令的地方是容器而非 WSL：把容器名与 `docker exec` 前缀记在
      本节顶部，后续所有命令统一带该前缀——**不要**一半在宿主一半在容器，排查时会互相甩锅
      - 证据（2026-10-31）：统一前缀 `docker exec deeptutor`（容器名 `deeptutor`），
        已写进本节开头的「实测值」表；宿主只跑 `docker` / `git` / 编辑。

### 6. 收尾

- [x] 提交 3 个文档改动：`docs-for-dev/WINDOWS_SETUP.md`（新增）、
      `docs-for-dev/README.md`、`docs-for-dev/FORK_MIGRATION.md`（各改 1 处链接）
      - 证据（2026-10-31）：见本节末尾「提交记录」。
- [x] 决定 `/root/DeepTutor`（WSL）与 `C:\Users\hwolf\Desktop\project\DeepTutor`
      （Windows）谁是唯一真源；**不要再让两份同时编辑**，否则迟早分叉
      - 证据（2026-10-31）：**WSL `/root/DeepTutor` 为唯一真源**。Windows 副本
        `git log -1` 同样是 `93957e5c`，但 `git status --short` 有 **1613 条**
        （1612 M + 1 ??）；逐文件 `git diff --numstat` 全是「等量增删」（如
        `.dockerignore 159/159`），`--ignore-all-space` 过滤后只剩 2 个文件——
        即 **1610 条是 CRLF 换行噪声**（该副本 `core.autocrlf` 未设）。
        本次未删除 Windows 副本，只把它当只读历史快照；后续编辑一律在 WSL 侧，
        Windows 侧要看结果请 `git pull` 或重新 clone（`core.autocrlf false`）。
- [x] 回到 §7 把「dev 栈构建 + `/health/ready`」那行改成 ✅，并补耗时与报错原文
      - 证据（2026-10-31）：§7 已改 ✅ 并补 `npm error code ECONNRESET/ETIMEDOUT` 原文、
        约 20 分钟耗时、4 容器 healthy 与 `/health/ready` 200。

**提交记录（2026-10-31）**：3 个文档改动合并为一次提交，subject 为
`docs(windows): add Windows/WSL2 setup guide and verify the container dev stack`
（本行随该提交入库，故不写死 hash——用 `git log -1 --format=%h` 查）。

### 已知风险（动手前先读）

1. **构建期网络 ≠ 拉取期代理**：daemon 的 `HTTP_PROXY` 只管 `docker pull`，
   `docker build` 里 `npm ci` / `pip install` 走的是构建容器自己的网络栈（§3.4 末尾）。
2. **镜像内进程是 uid 1000（`deeptutor`）**，而挂进来的 `/root/DeepTutor` 属主是 root：
   只读挂载没事，一旦需要容器内写文件（跑测试生成缓存、`next dev` 写 `.next`）就会
   `Permission denied`。要么挂载时加 `:rw` 并放宽目录权限，要么容器以 root 运行。
3. **`data/user/settings/*.json` 里有真实密钥**：迁移、复制、提交前先确认路径
   （见 [FORK_MIGRATION.md](FORK_MIGRATION.md) §1.2）。
4. **中文 `findstr`、UTF-16 输出、`Enable Pending` + 快速启动** 这些 Windows 侧的坑
   已经踩完并写进 §3.1 与 §6，换机器时照抄，不要重新发明。
5. **在容器里以 root 跑测试会污染两处，随后 app 起不来或 reload 静默失效**（2026-10-31 实测）：
   - `/tmp/deeptutor-environment-<hash>.lock` 被 root 创建（mode 644），uid 1000 的后端
     `_file_lease` 打不开 → **`Application startup failed. Exiting.` + 前端 502**
     （`PermissionError: '/tmp/deeptutor-environment-….lock'`）；重启容器也没用，必须先
     `docker exec -u root deeptutor rm -f /tmp/deeptutor-environment-*.lock`。
   - `data/` 下出现 root 所有的目录 → 后端访问
     `/app/data/users/<user>/user/settings` 抛 `PermissionError`，随后 `WatchFiles` 抛
     `WatchfilesRustInternalError … Permission denied`，**热重载从此静默失效**（服务还在，
     只是不再重启）。修法：`chown -R 1000:1000 data/` 后重启。
   结论：测试请用 `docker exec -u deeptutor`，或跑完后无条件
   `chown -R 1000:1000 data && rm -f /tmp/deeptutor-environment-*.lock` 再重启。
6. **镜像里没有 `tests/`、`README.md`、`Dockerfile`、`.git`**：dev 栈的 `/app` 只是镜像内容
   ＋ 只读源码挂载，`docker-compose.dev.yml` 没挂 `tests/`。因此
   - 跑测试前要 `docker cp tests deeptutor:/app/tests`、`docker cp README.md deeptutor:/app/README.md`，
     否则 `pytest tests` 会因 `tests/cli/test_provider_cli.py` 读不到 `/app/README.md` 而
     **collection error 直接中断**；
   - `npm run test:node` 里 4 条断言会假失败（没有 `.git`、没有 `/app/Dockerfile`、
     `web/public` 是 `:ro` 写不了 pdfjs）。
7. **前端单测必须 `NODE_ENV=test`**：镜像继承 `ENV NODE_ENV=production`，React 19 的生产构建
   没有 `act`，`npm run test:unit` 会出现 730 条 `TypeError: React.act is not a function`
   （866 failed / 970）；加 `NODE_ENV=test` 后 995/995 全绿。别把它当成代码坏了。
8. **`python3 scripts/docker_compose.py` 与 `docker compose ps` 的 compose 文件不一致**：
   仓库里同时有 `compose.yaml` 和 `docker-compose.yml`，裸跑 `docker compose ps/logs` 会用
   `compose.yaml` 并给出 `Found multiple config files … Using /root/DeepTutor/compose.yaml`
   警告；要复现本节状态，一律走 `python3 scripts/docker_compose.py -f docker-compose.yml
   -f docker-compose.dev.yml …`。
