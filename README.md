# Rana Agent

自托管、持续在线的个人 Agent，面向 NAS 与 Android 使用场景。产品体验对标 Dot：不仅回答问题，也把对话延伸为长期目标、持久任务、定时日程、可检索记忆与需要审批的工具动作。

支持 **小米 MiMo** 与 **MiniMax** 两套云端 Provider，覆盖文本、ASR 与 TTS；默认使用 MiMo。本地 GPU 可通过 Provider 适配层扩展，不是基础部署前提。服务端与执行器均提供 `amd64` / `arm64` 多架构镜像构建路径。

## 核心能力

- **持久对话与目标**：会话、消息、目标和任务落入 SQLite，重启后恢复队列、租约与执行状态。
- **持续任务运行时**：优先级调度、公平并发、心跳、超时、取消、重试、幂等键与任务事件。
- **长期记忆与日程**：记忆检索、用户设置、cron 日程和时区配置。
- **实时语音**：WebSocket 二进制音频帧，VAD/end 结束收音，ASR → 流式 LLM → 分句 TTS，支持顺序播放、barge-in 和回合隔离。
- **独立执行器**：server 通过内部 Bearer token 调用 executor；文件、命令与浏览器能力集中在受控工作区。
- **审批与连接器**：高风险动作可暂停等待审批，外部连接配置按用户隔离并脱敏。
- **Android App**：Flutter 客户端位于 `app/`，复用同一套 REST 与语音协议，不直接访问数据库或 executor。

## 快速启动（NAS / Docker Compose）

最简单的方式是使用一键部署器（自动初始化配置、生成密钥、启动服务与看门狗）：

```sh
export AGENT_MIMO_API_KEY='你的MiMo密钥'
python scripts/deploy.py --provider mimo --yes
```

MiniMax 使用 `--provider minimax`；MiniMax 大模型 + 小米 ASR/TTS 使用 `--provider hybrid`，并同时提供两个 API Key。自动化部署步骤见 [AGENTS.md](AGENTS.md)。

手工部署的前置条件：Docker Engine、Compose v2，以及可用的 MiMo 或 MiniMax API Key。

```sh
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，至少替换：

```dotenv
AGENT_ADMIN_PASSWORD=独立强密码
AGENT_SECRET_KEY=至少32字节随机值
AGENT_EXECUTOR_TOKEN=另一份独立随机值
AGENT_AI_PROVIDER=mimo
AGENT_MIMO_API_KEY=你的MiMo密钥
# 使用 MiniMax 时改为 AGENT_AI_PROVIDER=minimax，并设置 AGENT_MINIMAX_API_KEY
```

可在 POSIX 环境用 `openssl rand -hex 32` 分别生成签名密钥和内部令牌。随后：

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl --fail http://127.0.0.1:8080/healthz
```

服务默认监听 `8080`。Executor 仅在 Compose 网络内监听 `8081`，不会发布到宿主机。容器启动时先执行 `alembic upgrade head`，再启动 API；数据库迁移与中文 FTS5 索引结构均受版本管理。交互式 OpenAPI 文档由 FastAPI 暴露在 `/docs`，存活与就绪检查分别为 `/healthz`、`/readyz`。

Windows 首次准备配置：

```powershell
Copy-Item .env.example .env
pwsh -File scripts/backup.ps1   # 已有数据后，创建一致性备份
```

## Android App

移动端源码位于 `app/`，要求 Flutter 3.47+ / Dart 3.13+。通用构建命令：

```sh
cd app
flutter pub get
flutter build apk --release
```

当前已实测生成 Release APK：`app/build/app/outputs/flutter-apk/app-release.apk`（约 54.8 MB，Android 7.0+）。测试包使用 Android Debug 证书，便于直接安装；正式发布签名配置见 `app/README.md`。

在客户端的 API Base URL 配置中填写 `http(s)://NAS_HOST:8080/api/v1`（必须包含 `/api/v1`）。`AppConfig.defaultBaseUrl` 的 Android 模拟器默认值为 `http://10.0.2.2:8080/api/v1`，连接 NAS 时应改为实际地址。手机与 NAS 需网络互通。经反向代理使用时必须支持 WebSocket Upgrade，并为语音长连接配置足够的读取超时。生产使用 HTTPS/WSS。

## MiMo / MiniMax 与可选本地 GPU

默认配置来自根 `.env.example`：

| 配置 | 默认值 |
| --- | --- |
| `AGENT_MIMO_BASE_URL` | `https://api.xiaomimimo.com/v1` |
| `AGENT_MIMO_LLM_MODEL` | `mimo-v2.6-flash` |
| `AGENT_MIMO_ASR_MODEL` | `mimo-v2.5-asr` |
| `AGENT_MIMO_TTS_MODEL` | `mimo-v2.5-tts` |
| `AGENT_MIMO_VOICE` | `mimo_default` |

Provider 对运行时暴露统一的 `complete` / `stream_chat` 契约。切换 `AGENT_AI_PROVIDER=minimax` 后，使用 `MiniMax-M3.1-Flash`、`asr-1.0` 与 `speech-2.8-turbo`，App 的音频协议保持不变。也可设置 `AGENT_LLM_PROVIDER=minimax`、`AGENT_ASR_PROVIDER=mimo`、`AGENT_TTS_PROVIDER=mimo`，实现 **MiniMax 大模型 + 小米 ASR/TTS**。详细配置和官方接口见 [Provider 文档](docs/PROVIDERS.md)。

需要本地 GPU 时，可为 Ollama、vLLM 或其他 OpenAI 兼容端点增加 Provider 适配并注入运行时；当前 `.env.example` 的云端 URL 不是本地推理开关。这样 NAS 基础服务保持轻量，模型计算可独立放在 GPU 主机。

## 健康看门狗

一键部署默认启动 Watchdog：每 30 秒检查 Server/Executor，连续失败后重启，支持冷却与重启频率限制。手工启用：

```sh
docker compose --profile watchdog up -d
```

使用 `python scripts/deploy.py --no-watchdog` 可关闭。日志查看：`docker compose logs -f watchdog`。

## 可选 Chromium

默认 executor 不包含浏览器。需要网页任务时合并覆盖配置：

```sh
docker compose -f docker-compose.yml -f deploy/compose.browser.yml up -d --build
```

该覆盖配置通过 `INSTALL_BROWSER=true` 安装 Playwright/Chromium，并设置 executor 的 `BROWSER_HEADLESS=true`。`AGENT_MAX_BROWSER_SESSIONS=1` 是 server 侧保守的并发预算配置；当前 executor 按 task 隔离浏览器 context，还应结合任务并发与 `EXECUTOR_BROWSER_MEMORY_LIMIT` 控制实际资源占用。

## 备份与恢复

```sh
# POSIX：备份两个数据卷
./scripts/backup.sh
./scripts/restore.sh backups/rana-agent-YYYYMMDDTHHMMSSZ.tar.gz
```

```powershell
# Windows PowerShell
pwsh -File scripts/backup.ps1
pwsh -File scripts/restore.ps1 -ArchivePath .\backups\rana-agent-YYYYMMDDTHHMMSSZ.tar.gz
```

脚本会停止写入方、归档 server 与 executor 的 `/data` 卷，再恢复服务。根 `.env` 不在压缩包内，应单独作为敏感配置备份。恢复会替换目标卷内容，先保留当前状态的独立备份。

## 本地开发

Python 3.12–3.14：

```sh
python -m venv .venv
. .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
cp .env.example .env

# 终端一：API / Agent / MiMo Provider
python -m uvicorn personal_agent.main:app --app-dir server --reload --port 8080

# 终端二：独立 executor，读取 EXECUTOR_TOKEN / WORKSPACE_ROOT
python -m uvicorn app.main:app --app-dir executor --reload --port 8081

# 测试
python -m pytest

# 两个真实 HTTP 进程启动后的联调（替换为本地配置值）
python scripts/smoke_http.py --password '管理员密码' --internal-token '内部令牌'
```

本地运行时在 `.env` 中把 server 的 `AGENT_EXECUTOR_BASE_URL` 改为 `http://127.0.0.1:8081`，并额外加入 executor 实际读取的配置（值与 server 的内部令牌一致）：

```dotenv
AGENT_EXECUTOR_BASE_URL=http://127.0.0.1:8081
EXECUTOR_TOKEN=与AGENT_EXECUTOR_TOKEN相同
WORKSPACE_ROOT=./data/executor
```

Compose 已自动完成 token 映射、executor 工作区和容器内 URL 配置。

## 文档

- [REST API 与错误约定](docs/API.md)
- [实时语音帧协议与调试](docs/VOICE.md)
- [NAS、Buildx、多架构、备份恢复](docs/DEPLOYMENT.md)
- [运行时、数据模型与故障边界](docs/ARCHITECTURE.md)
- [MiMo / MiniMax Provider 配置](docs/PROVIDERS.md)
- [实测与验收记录](docs/VERIFICATION.md)

## 目录

```text
server/personal_agent/   FastAPI、Provider、任务运行时、语音与数据模型
executor/app/            独立工具执行服务
app/                     Flutter / Android 客户端
deploy/                  多阶段 Dockerfile 与可选浏览器 Compose 覆盖
scripts/                 PowerShell 与 POSIX 备份/恢复脚本
docs/                    中文接口和部署文档
docker-compose.yml       NAS 默认部署入口
```
