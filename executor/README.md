# Personal Agent Executor

基于 FastAPI 的内部工具执行服务，提供按 `task_id` 隔离的工作区、受控子进程、可选 Playwright 浏览器以及产物下载。项目仅包含 Executor，不包含调度器或 MiMo。

## 功能

- `GET /healthz`：健康检查，不需要认证。
- `POST /api/v1/execute`：内部 Bearer token 认证，统一返回 `ToolResult`。
- `workspace_list/read/write/delete`：任务目录隔离、路径穿越防护、文本编码及文件大小限制。
- `shell`：直接执行 argv，或将字符串经 `shlex` 拆分后执行；不经过登录 shell，不使用 `shell=True`。
- `browser`：可选 Playwright，支持 `open/read/click/fill/screenshot`，每个任务使用独立浏览器 context。
- `GET /api/v1/artifacts/{task_id}/{path:path}`：认证后下载任务产物。
- 同目录临时文件 + `fsync` + `os.replace` 原子写入；POSIX 任务目录权限为 `0700`，文件为 `0600`。
- 日志不记录工具参数、文件内容或进程输出，并脱敏 Bearer token、密码、secret、API key、Cookie 等字段。

## 环境与启动

要求 Python **3.12 或更高版本**。依赖包括 FastAPI、pydantic-settings、httpx 和 Uvicorn。

### Windows PowerShell

```powershell
cd executor
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:EXECUTOR_TOKEN = "replace-with-internal-token"
$env:WORKSPACE_ROOT = "$PWD\runtime-workspace"
.\.venv\Scripts\python.exe -m app
```

### Linux

```bash
cd executor
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export EXECUTOR_TOKEN='replace-with-internal-token'
export WORKSPACE_ROOT="$PWD/runtime-workspace"
.venv/bin/python -m app
```

默认监听 `0.0.0.0:8000`。开发默认 token 为 `dev-token`，部署时通过 `EXECUTOR_TOKEN` 覆盖。交互文档位于 `/docs`。

```bash
curl http://127.0.0.1:8000/healthz
# {"status":"ok"}
```

### 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `EXECUTOR_TOKEN` | `dev-token` | 内部 Bearer token；也兼容 `INTERNAL_TOKEN` |
| `WORKSPACE_ROOT` | `./workspace` | 所有任务目录的根路径 |
| `MAX_FILE_BYTES` | `1048576` | 单文件读写及截图大小上限 |
| `MAX_LIST_ENTRIES` | `1000` | 单次目录列表最大返回项数 |
| `SHELL_OUTPUT_BYTES` | `65536` | stdout、stderr 各自保留的最大字节数 |
| `DEFAULT_TIMEOUT_SECONDS` | `30` | 请求未指定时的工具超时 |
| `MAX_TIMEOUT_SECONDS` | `300` | 工具超时的配置上限 |
| `BROWSER_HEADLESS` | `true` | Playwright 是否使用无头模式 |
| `BROWSER_MAX_TEXT_CHARS` | `200000` | 浏览器文本最大返回字符数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `HOST` | `0.0.0.0` | 监听地址 |
| `PORT` | `8000` | 监听端口 |

`WORKSPACE_ROOT`、资源限制、超时、浏览器及日志配置也支持加 `EXECUTOR_` 前缀的形式，例如 `EXECUTOR_WORKSPACE_ROOT`、`EXECUTOR_MAX_FILE_BYTES`。配置可写入启动目录下的 `.env`，不要把生产 token 写入版本库。

## 执行 API

请求体：

```json
{
  "task_id": "task-001",
  "tool": "workspace_write",
  "arguments": {
    "path": "notes/result.txt",
    "content": "hello"
  },
  "timeout_seconds": 30
}
```

`task_id` 仅允许 ASCII 字母、数字、下划线和连字符，长度 1–128，且首字符必须是字母或数字。`timeout_seconds` 可省略。

调用示例：

```bash
curl -sS http://127.0.0.1:8000/api/v1/execute \
  -H 'Authorization: Bearer replace-with-internal-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "task_id":"task-001",
    "tool":"workspace_write",
    "arguments":{"path":"notes/result.txt","content":"hello"},
    "timeout_seconds":30
  }'
```

所有工具调用的 HTTP 200 响应均符合同一结构；工具失败通过 `ok=false` 和结构化 `error` 表示：

```json
{
  "task_id": "task-001",
  "tool": "workspace_write",
  "ok": true,
  "data": {
    "path": "notes/result.txt",
    "size": 5,
    "encoding": "utf-8"
  },
  "error": null,
  "artifacts": ["notes/result.txt"],
  "duration_ms": 2
}
```

认证失败返回 HTTP 401；请求模型校验失败返回 HTTP 422。`error.code` 包括 `INVALID_ARGUMENTS`、`PATH_TRAVERSAL`、`FILE_NOT_FOUND`、`FILE_TOO_LARGE`、`SHELL_TIMEOUT`、`NON_ZERO_EXIT`、`BROWSER_UNAVAILABLE` 等。

## 工作区工具

所有 `path` 都是任务目录内的 POSIX 风格相对路径，使用 `/` 分隔。绝对路径、驱动器路径、反斜杠、`..` 和越界符号链接会被拦截。不同 `task_id` 的文件互不可见。

| 工具 | arguments | 成功 data |
|---|---|---|
| `workspace_list` | `path="."`、`recursive=false`、`limit` | `path`、`entries`、`truncated`、`limit` |
| `workspace_read` | `path`、`encoding="utf-8"` | `path`、`content`、`size`、`encoding` |
| `workspace_write` | `path`、`content`、可选 `encoding` | `path`、`size`、`encoding`，并产生 artifact |
| `workspace_delete` | `path`、`recursive=false` | `path`、`deleted=true`、`type` |

目录列表项形如：

```json
{"path":"notes/result.txt","type":"file","size":5}
```

删除非空目录需显式传入 `recursive=true`。读取和写入按编码后的字节数执行 `MAX_FILE_BYTES` 限制。

## shell 工具

### argv（推荐）

```json
{
  "task_id": "task-shell",
  "tool": "shell",
  "arguments": {
    "argv": ["python", "-c", "from pathlib import Path; print(Path.cwd())"]
  },
  "timeout_seconds": 10
}
```

### 字符串 command

```json
{
  "task_id": "task-shell",
  "tool": "shell",
  "arguments": {
    "command": "python -c \"print('hello')\""
  },
  "timeout_seconds": 10
}
```

`argv` 与 `command` 必须且只能提供一个。字符串仅被 `shlex.split` 拆成参数，随后仍由 `asyncio.create_subprocess_exec` 直接启动；`&&`、`;`、管道和重定向不会被 shell 解释。Windows 上优先使用 argv，以避免不同命令行解析规则的差异。

可选 `cwd` 是任务目录内的相对路径，默认为 `.`。子进程 stdin 为 `DEVNULL`，stdout/stderr 并发读取，避免管道写满导致死锁。超时后 POSIX 终止整个进程组，Windows 终止子进程。

```json
{
  "exit_code": 0,
  "stdout": "hello\n",
  "stderr": "",
  "stdout_bytes": 6,
  "stderr_bytes": 0,
  "stdout_truncated": false,
  "stderr_truncated": false,
  "output_limit_bytes": 65536
}
```

超限输出仍会被完整排空，但响应仅保留每路前 `SHELL_OUTPUT_BYTES` 字节，并用 `*_truncated` 和 `*_bytes` 表示截断状态及原始总字节数。非零退出码保留在 `data.exit_code`，同时返回 `NON_ZERO_EXIT`；超时返回 `SHELL_TIMEOUT` 且 `exit_code=null`。

工作区隔离适用于 workspace 与 artifact API。shell 以任务目录作为 cwd，但不替代操作系统沙箱；容器部署用于提供进程边界。

## browser 工具（可选）

安装 Playwright 包和 Chromium：

```bash
python -m pip install -e '.[browser]'
playwright install chromium
```

Linux 服务器需要系统依赖时可使用：

```bash
playwright install --with-deps chromium
```

未安装包或 Chromium 启动失败时，工具返回 HTTP 200、`ok=false` 和 `error.code=BROWSER_UNAVAILABLE`，不影响其他工具。

所有浏览器动作通过 `arguments.action` 指定。同一个 `task_id` 会复用独立 context/page，因此可依次执行：

```json
{"action":"open","url":"https://example.com","wait_until":"domcontentloaded"}
{"action":"fill","selector":"input[name=q]","value":"Personal Agent"}
{"action":"click","selector":"button[type=submit]"}
{"action":"read","selector":"body"}
{"action":"screenshot","path":"screens/result.png","full_page":true}
```

- `open`：必需 `url`；`wait_until` 支持 `load`、`domcontentloaded`、`networkidle`。
- `read`：可选 `url` 和 `selector`，默认读取 `body` 文本。
- `click`：必需 `selector`。
- `fill`：必需 `selector` 和字符串 `value`（也接受 `text`）。
- `screenshot`：可选 `path`（默认 `screenshot.png`）和 `full_page`；截图经原子写入保存，路径加入 `artifacts`。
- 后续动作可直接操作当前页面，也可提供 `url` 先导航。没有现有会话且未提供 `url` 时返回 `BROWSER_SESSION_NOT_FOUND`。

服务关闭时会清理所有浏览器 context 和 Playwright 进程。

## 产物下载

`workspace_write` 和 `browser screenshot` 返回任务相对路径。下载时拼接 URL，并携带同一 Bearer token：

```bash
curl -OJ http://127.0.0.1:8000/api/v1/artifacts/task-001/notes/result.txt \
  -H 'Authorization: Bearer replace-with-internal-token'
```

产物路径使用相同的任务隔离和路径穿越检查；文件不存在返回 HTTP 404，路径无效返回 HTTP 400。

## 测试

```bash
cd executor
python -m pip install -r requirements-dev.txt
python -m pytest
```

测试覆盖：

- 健康检查、Bearer token 缺失/错误/错误 scheme、产物认证；
- 多种相对/绝对路径穿越、任务隔离、读写列表删除、原子临时文件清理；
- 文件大小限制、POSIX 目录和文件权限、产物下载；
- argv 与 shlex 执行、shell 超时、非零退出码、stdout/stderr 独立截断；
- 浏览器不可用结构化错误和日志凭据脱敏。

Windows 本地运行时，POSIX 权限位专项测试会跳过，其余 shell 和 API 测试均直接执行真实子进程。

## Docker

基础镜像使用 `python:3.12-slim`，以 UID/GID `10001` 的非 root 用户运行，工作区位于 `/workspace`。

```bash
docker build -t personal-agent-executor ./executor
docker run --rm -p 8000:8000 \
  -e EXECUTOR_TOKEN='replace-with-internal-token' \
  -v executor-workspace:/workspace \
  personal-agent-executor
```

构建包含 Chromium 的镜像：

```bash
docker build \
  --build-arg INSTALL_PLAYWRIGHT=true \
  -t personal-agent-executor:browser ./executor
```

容器健康检查访问 `/healthz`。Linux 上 shell 使用独立进程会话，超时时终止进程组。

## 目录结构

```text
executor/
├── app/
│   ├── main.py            # FastAPI 路由、应用工厂、产物下载
│   ├── service.py         # 工具分发与 ToolResult 统一封装
│   ├── workspace.py       # 隔离路径、大小限制、原子写入
│   ├── shell.py           # argv/shlex、进程超时、流式输出限长
│   ├── browser.py         # 可选 Playwright 会话与动作
│   ├── security.py        # Bearer token 校验
│   ├── log_redaction.py   # 日志脱敏
│   ├── config.py          # pydantic-settings 配置
│   └── models.py          # 请求、响应和错误模型
├── tests/
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
└── Dockerfile
```
