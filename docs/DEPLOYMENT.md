# NAS 部署指南

本文以根目录的 `docker-compose.yml` 为准，服务入口为 `personal_agent.main:app`（端口 `8080`）和 `executor/app/main.py` 中的 `app`（端口 `8081`）。两个容器均通过 `GET /healthz` 做健康检查。

## 1. 部署拓扑

| 组件 | 镜像/构建文件 | 端口 | 持久卷 |
| --- | --- | --- | --- |
| `server` | `deploy/server.Dockerfile` | 宿主机 `8080` → 容器 `8080` | `agent_data:/data` |
| `executor` | `deploy/executor.Dockerfile` | 仅容器网络内 `8081`，不映射宿主机 | `executor_data:/data` |
| Chromium（可选） | 合并 `deploy/compose.browser.yml` 后编入 executor | 不单独开放端口 | 使用 executor 临时目录 |

`server` 与 `executor` 通过 Compose 的 `backend` 内部网络通信。`frontend` 网络提供 MiMo、RSS、邮件或网页任务所需的出站连接；executor 不发布宿主机端口。SQLite 数据库、WAL 文件与产物保存在 `/data`，不要放在容器可写层。

## 2. 前置条件

- 支持 named volume 的 Docker Engine 24+ 与 Docker Compose v2。
- NAS 架构为 `linux/amd64` 或 `linux/arm64`；镜像内 Python 为 3.12。
- 默认模型使用小米 MiMo 云，需要有效的 `AGENT_MIMO_API_KEY` 与可访问的 `AGENT_MIMO_BASE_URL`。使用 MiniMax 时设置 `AGENT_AI_PROVIDER=minimax` 和 `AGENT_MINIMAX_API_KEY`，完整字段见 [PROVIDERS.md](PROVIDERS.md)。
- 基础部署不需要 GPU。浏览器任务建议为 executor 预留更多内存，并单独启用下方浏览器覆盖文件。

## 3. 首次启动

### POSIX / NAS SSH

```sh
cp .env.example .env
chmod 600 .env
# 分别生成签名密钥和 executor 内部令牌，将输出写回 .env
openssl rand -hex 32
openssl rand -hex 32
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
# 安装了 Python 时可生成两份随机值，并写入 .env
python -c "import secrets; print(secrets.token_hex(32))"
python -c "import secrets; print(secrets.token_hex(32))"
```

至少核对 `.env` 中这些值：

```dotenv
AGENT_ADMIN_USERNAME=admin
AGENT_ADMIN_PASSWORD=替换为独立强密码
AGENT_SECRET_KEY=替换为至少32字节的随机十六进制值
AGENT_EXECUTOR_TOKEN=替换为另一份独立随机值
AGENT_MIMO_API_KEY=替换为MiMo密钥
AGENT_DATA_DIR=./data
AGENT_EXECUTOR_BASE_URL=http://executor:8081
```

Compose 会把容器内 `AGENT_DATA_DIR` 固定覆盖为 `/data`，并把 `AGENT_EXECUTOR_BASE_URL` 固定为 `http://executor:8081`；`.env.example` 中的本地路径仍适用于非容器开发。`.env` 同时用于 Compose 变量插值和容器环境变量，不要提交到版本库。

启动并验证：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
docker compose logs --tail=100 server executor
```

首次登录使用 `AGENT_ADMIN_USERNAME` / `AGENT_ADMIN_PASSWORD`。若关闭管理员密码的环境变量注入逻辑，则以服务端启动日志的一次性提示为准；本仓库提供的 Compose 默认要求显式设置密码。

## 4. Compose 专用变量

下列变量只影响 Compose 或镜像选择，可直接追加到根 `.env`；它们不是服务端 Pydantic 配置项。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SERVER_BIND_IP` | `0.0.0.0` | 服务监听地址；仅本机访问时设为 `127.0.0.1` |
| `SERVER_PORT` | `8080` | NAS 对外 HTTP/WebSocket 端口 |
| `IMAGE_TAG` | `0.1.0` | server/executor 的共同镜像标签 |
| `SERVER_IMAGE` | `personal-agent-server` | server 镜像仓库名 |
| `EXECUTOR_IMAGE` | `personal-agent-executor` | executor 镜像仓库名 |
| `SERVER_CPU_LIMIT` / `SERVER_MEMORY_LIMIT` | `2.0` / `2g` | server 资源上限 |
| `EXECUTOR_CPU_LIMIT` / `EXECUTOR_MEMORY_LIMIT` | `2.0` / `2g` | executor 资源上限 |
| `INSTALL_BROWSER` | `false` | 构建 executor 时是否安装 Playwright/Chromium |

模型、任务池、线程池和超时使用 `.env.example` 中的 `AGENT_*` 项。例如 `AGENT_MAX_ASYNC_TASKS`、`AGENT_MAX_THREAD_WORKERS`、`AGENT_MAX_BROWSER_SESSIONS` 和 `AGENT_TASK_TIMEOUT_SECONDS` 会传入两个服务；实际生效项以各服务配置为准。

## 5. 可选浏览器执行环境

默认 executor 镜像不含浏览器。以下命令合并覆盖文件，在构建阶段安装 Playwright 与 Chromium，并设置 executor 实际读取的 `BROWSER_HEADLESS=true`：

```sh
docker compose \
  -f docker-compose.yml \
  -f deploy/compose.browser.yml \
  up -d --build executor server
```

覆盖文件同时把 executor 默认内存上限提高到 `4g`；可通过 `EXECUTOR_BROWSER_MEMORY_LIMIT` 调整。`AGENT_MAX_BROWSER_SESSIONS=1` 是 server 侧的保守预算配置；当前 executor 按 task 隔离浏览器 context，还需结合 `AGENT_MAX_ASYNC_TASKS` 控制实际并发。再次只使用根 `docker-compose.yml` 执行 `up -d --build` 会回到无浏览器配置。

覆盖文件中的 `BROWSER_HEADLESS` 与 executor 配置项一一对应；BrowserTool 按 task 隔离 context。浏览器并发还受任务调度与容器资源共同约束，不能仅凭安装 Chromium 推断浏览器工具已经注册。

## 6. 本地构建与多架构发布

先启用 Buildx：

```sh
docker buildx create --name personal-agent-builder --driver docker-container --use
docker buildx inspect --bootstrap
```

单架构本地验证（把 `PLATFORM` 替换为 `linux/amd64` 或 `linux/arm64`）：

```sh
docker buildx build --platform PLATFORM --load \
  -f deploy/server.Dockerfile -t personal-agent-server:0.1.0 .
docker buildx build --platform PLATFORM --load \
  -f deploy/executor.Dockerfile -t personal-agent-executor:0.1.0 .
docker compose up -d
```

一次构建并推送双架构镜像（将 `OWNER` 替换为实际仓库命名空间）：

```sh
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/server.Dockerfile \
  -t ghcr.io/OWNER/personal-agent-server:0.1.0 --push .
docker buildx build --platform linux/amd64,linux/arm64 \
  -f deploy/executor.Dockerfile \
  -t ghcr.io/OWNER/personal-agent-executor:0.1.0 --push .
```

浏览器变体使用单独标签，避免覆盖 slim 镜像：

```sh
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg INSTALL_BROWSER=true \
  -f deploy/executor.Dockerfile \
  -t ghcr.io/OWNER/personal-agent-executor:0.1.0-browser --push .
```

NAS 拉取远程镜像时，在 `.env` 中设置：

```dotenv
SERVER_IMAGE=ghcr.io/OWNER/personal-agent-server
EXECUTOR_IMAGE=ghcr.io/OWNER/personal-agent-executor
IMAGE_TAG=0.1.0
```

然后运行 `docker compose pull && docker compose up -d`。Compose 文件仍保留 `build` 段，便于源码构建；已经拉到匹配标签的镜像不会要求本机重新构建。

## 7. 备份与恢复

脚本会自动发现 Compose 项目实际使用的卷名，因此支持自定义 `COMPOSE_PROJECT_NAME`。备份时先停止 server/executor，再归档两个 `/data` 卷，最后恢复容器；这保证 SQLite 主库、WAL 与产物处于同一维护时点。

POSIX：

```sh
chmod +x scripts/backup.sh scripts/restore.sh
./scripts/backup.sh
BACKUP_DIR=/volume1/backups/personal-agent ./scripts/backup.sh
./scripts/restore.sh backups/personal-agent-YYYYMMDDTHHMMSSZ.tar.gz
```

Windows PowerShell：

```powershell
pwsh -File scripts/backup.ps1
pwsh -File scripts/backup.ps1 -BackupDirectory D:\PersonalAgentBackups
pwsh -File scripts/restore.ps1 -ArchivePath .\backups\personal-agent-YYYYMMDDTHHMMSSZ.tar.gz
```

说明：

- 备份格式为 `personal-agent-<UTC时间>.tar.gz`，包含 `server/` 与 `executor/` 两个顶层目录。
- 脚本不包含根 `.env`。请把 `.env` 作为单独的敏感配置备份保存，恢复密码、签名密钥与内部令牌时需要它。
- 默认辅助镜像为 `alpine:3.21`；离线环境可预拉取后用 `BACKUP_IMAGE` 环境变量或 `-BackupImage` 参数替换。
- 恢复会清空两个目标卷现有内容。先把当前状态备份到另一目录，再执行恢复；脚本会检查压缩包路径穿越与顶层目录。
- `DOCKER` 环境变量或 `-Docker` 参数可指定 Docker CLI 路径；`COMPOSE_FILE`、`COMPOSE_PROJECT_NAME` 继续由 Docker Compose 原生处理。

可配置每日维护窗口，例如 NAS cron：

```cron
15 3 * * * cd /volume1/docker/personal-agent && BACKUP_DIR=/volume1/backups/personal-agent ./scripts/backup.sh >> /volume1/backups/personal-agent/backup.log 2>&1
```

## 8. 升级与回滚

1. 运行备份脚本并单独保存 `.env`。
2. 固定当前 `IMAGE_TAG`，记录正在使用的版本。
3. 源码部署运行 `docker compose up -d --build`；镜像部署更新 `IMAGE_TAG` 后运行 `docker compose pull && docker compose up -d`。
4. Server 每次启动自动执行 `python -m alembic upgrade head`；可在维护窗口用 `docker compose run --rm server python -m alembic upgrade head` 单独执行。迁移状态可用 `python -m alembic current` 检查，模型差异检查命令为 `python -m alembic check`。
5. 检查 `docker compose ps`、`GET /healthz`、登录、一次文本任务和一次语音回合。
6. 回滚时恢复原 `IMAGE_TAG`；若数据库结构或产物格式已经变化，停止服务并恢复对应备份。

## 9. 反向代理与 Android 访问

Android App 的 API Base URL 使用 `http(s)://NAS_HOST:SERVER_PORT/api/v1`（必须包含 `/api/v1`）；普通浏览器 REST 客户端也采用这一前缀。反向代理必须同时转发普通 HTTP 和 WebSocket 升级请求，并为语音长连接调大读取超时。建议仅在代理层终止 TLS，不直接暴露 executor 的 `8081`。

最小 Nginx 位置块示例：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 300s;
}
```

若把 `SERVER_BIND_IP` 设置为 `127.0.0.1`，Nginx 必须与 Docker 端口映射位于同一网络命名空间；常规 NAS 容器部署可保留默认绑定地址并通过防火墙限制来源。

## 10. 健康看门狗

```sh
docker compose --profile watchdog up -d
docker compose logs -f watchdog
```

Watchdog 检查 Server `/readyz` 与 Executor `/healthz`，默认连续 3 次失败后重启容器；冷却 300 秒，30 分钟内最多重启 3 次。通过 `/var/run/docker.sock` 调用 Docker API，不需要云密钥或业务数据卷。容器名由 `COMPOSE_PROJECT_NAME` 固定为 `personal-agent-server-1` / `personal-agent-executor-1`。一键部署器默认启用，`--no-watchdog` 可关闭。

## 11. 排查

```sh
# 展开后的配置与必需变量检查
docker compose config

# 服务状态与健康检查详情
docker compose ps
docker inspect --format '{{json .State.Health}}' CONTAINER_ID

# 日志与资源
docker compose logs --since=15m server
docker compose logs --since=15m executor
docker stats

# 容器网络内验证 executor，不在宿主机寻找 8081 映射
docker compose exec server python -c "import urllib.request; print(urllib.request.urlopen('http://executor:8081/healthz').read().decode())"
```

常见问题：

- **容器反复重启**：检查必需变量、MiMo 密钥、数据卷权限和 `/healthz` 日志。
- **server 等待 executor**：executor 健康检查未通过；先排查 executor，而不是绕过 `depends_on`。
- **SQLite 只读/锁定**：确认 `/data` 可写，备份恢复后文件 UID/GID 仍为容器用户 `10001:10001`，并检查磁盘余量。
- **语音升级失败**：确认代理的 WebSocket 头和超时；不要把二进制语音帧当作普通表单提交。
- **浏览器内存不足**：使用浏览器覆盖文件，降低 `AGENT_MAX_BROWSER_SESSIONS` 与 `AGENT_MAX_ASYNC_TASKS`，并调整 `EXECUTOR_BROWSER_MEMORY_LIMIT`。
