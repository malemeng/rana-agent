# Agent Deployment Runbook

本文件面向自动化部署 Agent，目标是让 NAS 上的 Personal Agent 可重复部署、可验证、可恢复。所有命令在项目根目录执行。不要提交或打印 `.env`、API Key、Access Token、Refresh Token。

## 1. 前置检查

- Docker Engine 与 Compose v2。
- NAS 可访问所选模型 API；语音默认走云端，不需要本地模型权重。
- 默认端口 `8080`，持久数据在 Compose 的 `agent_data`、`executor_data` 卷。
- Python 3.12+ 用于运行部署器；纯 NAS 容器运行时不依赖宿主机 Python 包。

```sh
python --version
docker version
docker compose version
```

## 2. 一键部署

### MiMo 全栈

```sh
export AGENT_MIMO_API_KEY='[MI MO_KEY]'
python scripts/deploy.py --provider mimo --yes
```

### MiniMax 全栈

```sh
export AGENT_MINIMAX_API_KEY='[MINIMAX_KEY]'
python scripts/deploy.py --provider minimax --yes
```

MiniMax 中国区增加 `--minimax-region cn`，自动使用 `https://api.minimax.cn/v1` 的 LLM、ASR 与 TTS 接口。

### MiniMax LLM + 小米 ASR/TTS

```sh
export AGENT_MINIMAX_API_KEY='[MINIMAX_KEY]'
export AGENT_MIMO_API_KEY='[MI MO_KEY]'
python scripts/deploy.py --provider hybrid --yes
```

部署器会：

1. 从 `.env.example` 和已有 `.env` 合并配置。
2. 备份已有 `.env`，以 0600 权限写入新配置。
3. 生成缺失的管理员密码、JWT 密钥和 Executor 内部令牌。
4. 构建并启动 Server、Executor 与 Watchdog。
5. 等待 `GET /healthz` 返回 200，再输出 App Base URL。

可用参数：

| 参数 | 用途 |
| --- | --- |
| `--project-dir PATH` | 指定项目目录 |
| `--port PORT` | 修改服务端口 |
| `--admin-username NAME` / `--admin-password VALUE` | 指定管理员凭据 |
| `--no-start` | 只生成配置 |
| `--no-build` | 使用现有镜像启动 |
| `--no-watchdog` | 禁用健康看门狗 |

无人值守时必须传 `--yes`。API Key 优先从同名环境变量读取，避免进入命令历史。

## 3. 看门狗

一键部署默认启用 `watchdog` profile。手工启用：

```sh
docker compose --profile watchdog up -d
```

- 每 30 秒检查 Server `/readyz` 和 Executor `/healthz`。
- 连续 3 次失败后通过 Docker Socket 重启对应容器。
- 同一服务重启冷却 300 秒；30 分钟窗口内最多 3 次。
- 容器名为 `${COMPOSE_PROJECT_NAME}-server-1` 和 `${COMPOSE_PROJECT_NAME}-executor-1`。
- 看门狗只连接内部后端网络与 `/var/run/docker.sock`，不持有模型密钥。

查看日志：

```sh
docker compose logs -f watchdog
```

## 4. 验收

```sh
curl --fail http://127.0.0.1:8080/healthz
docker compose ps
python -m pytest
```

App Base URL：`http(s)://NAS_HOST:8080/api/v1`。语音使用根路径 `/ws/voice`，事件使用 `/ws/events`，反向代理需启用 WebSocket Upgrade。

真实 MiMo 全链路（已有 `AGENT_MIMO_API_KEY` 时）：

```sh
python scripts/verify_mimo_live.py
```

脚本只输出回答、PCM 字节数和识别文本，不输出密钥。

## 5. 升级与恢复

```sh
./scripts/backup.sh
docker compose up -d --build
```

回滚使用固定镜像版本；数据库或工件格式变化时先恢复对应备份。不要为自动部署创建 Git commit、push 或发布，除非操作者明确提出。
