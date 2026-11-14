# API 参考

## 1. 基础约定

- Server Base URL：`http(s)://NAS_HOST:PORT/api/v1`。
- 请求与响应默认 `application/json; charset=utf-8`；TTS 与文件下载除外。
- 认证后请求携带：`Authorization: Bearer <access_token>`。
- 时间字段以 ISO 8601 字符串返回；cron 日程另有 IANA 时区字段，如 `Asia/Shanghai`。
- 列表接口使用 `limit` / `offset`。没有显式分页包装时，响应直接是 JSON 数组。
- 资源访问同时校验 ID 与当前 `user_id`；其他用户的资源按 404 处理，避免通过响应枚举归属。
- FastAPI 的业务错误一般为 `{"detail":"错误说明"}`；请求校验失败为 HTTP 422，`detail` 是逐字段错误数组。
- 完整实时 schema 以运行实例 `/docs` 和 `/openapi.json` 为准；语音二进制协议见 [VOICE.md](VOICE.md)。

## 2. 认证

Access token 默认 30 分钟有效；refresh token 默认 30 天、单次使用。每次 refresh 都撤销旧会话并返回一对新 token，客户端应原子替换本地保存的两个值。

### 登录

`POST /auth/login`，无需 Bearer。

```json
{
  "username": "admin",
  "password": "你的管理员密码"
}
```

```sh
curl --fail http://127.0.0.1:8080/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"你的管理员密码"}'
```

响应：

```json
{
  "access_token": "<JWT>",
  "refresh_token": "<单次使用的随机令牌>",
  "token_type": "bearer",
  "expires_in": 1800
}
```

用户名或密码错误返回 401；未配置 JWT 签名密钥返回 503。

### 旋转 refresh token

`POST /auth/refresh`，无需旧 access token。

```json
{"refresh_token":"<当前refresh_token>"}
```

成功返回同上的 `TokenResponse`。旧 refresh token 立即失效；过期、已撤销或不存在的 token 返回 401。网络重试时应先检查本地是否已收到并保存新 token，避免用已经旋转的旧值反复刷新。

### 当前用户

`GET /auth/me`，需要 Bearer。

```json
{
  "id": "用户UUID",
  "username": "admin",
  "is_admin": true,
  "created_at": "2026-01-01T00:00:00"
}
```

### 登出

`POST /auth/logout`，需要 Bearer，body 为：

```json
{"refresh_token":"<要撤销的refresh_token>"}
```

撤销属于当前用户的会话，成功返回 204 且没有响应体。客户端随后删除本地 access/refresh token。

## 3. 会话与消息

资源前缀：`/conversations`。所有操作需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/conversations?limit=100&offset=0` | 200 | 按 `updated_at` 倒序列出当前用户会话 |
| POST | `/conversations` | 201 | 创建会话 |
| GET | `/conversations/{conversation_id}` | 200 | 会话详情 |
| PATCH | `/conversations/{conversation_id}` | 200 | 局部更新标题、摘要或 agent |
| DELETE | `/conversations/{conversation_id}` | 204 | 删除会话 |
| GET | `/conversations/{conversation_id}/messages?limit=200&offset=0` | 200 | 按创建时间正序读取消息 |
| POST | `/conversations/{conversation_id}/messages` | 202 | 写入用户消息并创建 Agent 任务 |

创建会话：

```json
{
  "title": "新对话",
  "summary": "",
  "agent_id": null
}
```

`title` 为 1–200 字符；`agent_id` 可省略，但必须属于当前用户。PATCH 只需发送要修改的字段。

发送消息：

```json
{"content":"请整理本周目标并给出下一步。"}
```

`content` 为 1–100000 字符。202 响应同时返回持久消息和异步任务：

```json
{
  "message": {
    "id": "消息UUID",
    "conversation_id": "会话UUID",
    "role": "user",
    "content": "请整理本周目标并给出下一步。",
    "status": "completed",
    "turn_id": null,
    "created_at": "2026-01-01T00:00:00"
  },
  "task": {
    "id": "任务UUID",
    "status": "queued",
    "progress": 0.0
  }
}
```

这里的 202 表示任务已接受，不表示助手已经回答。客户端随后轮询 `GET /tasks/{task_id}` 或订阅事件，并重读消息列表获取最终助手消息。

## 4. 长期目标

资源前缀：`/goals`。所有操作需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/goals?status=active&limit=100&offset=0` | 200 | 目标列表，可按状态筛选 |
| POST | `/goals` | 201 | 创建 Goal 与根 Task |
| GET | `/goals/{goal_id}` | 200 | 目标详情 |
| PATCH | `/goals/{goal_id}` | 200 | 更新标题、输入、优先级或状态 |
| POST | `/goals/{goal_id}/status` | 200 | 只更新状态 |
| DELETE | `/goals/{goal_id}` | 204 | 删除目标 |

创建：

```sh
curl --fail http://127.0.0.1:8080/api/v1/goals \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "title":"每周复盘",
    "input":"汇总本周任务，识别阻塞并生成下周计划",
    "priority":50,
    "idempotency_key":"weekly-review-2026-W01"
  }'
```

字段约束：

- `title`：1–200 字符。
- `input`：1–200000 字符。
- `priority`：0–100，默认 50。
- `idempotency_key`：可选，最长 120；在用户范围内防止重复创建。

201 响应包含 `goal` 和 `task` 两个完整对象。Goal 状态为 `active`、`paused`、`completed`、`cancelled` 之一；转为 completed 时服务端补写 `completed_at`。

```json
{"status":"paused"}
```

## 5. 任务

资源前缀：`/tasks`。所有操作需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/tasks?status=queued&goal_id=...&limit=100&offset=0` | 200 | 当前用户任务列表，可按状态/目标筛选 |
| GET | `/tasks/{task_id}` | 200 | 任务状态、进度、结果与错误 |
| PATCH | `/tasks/{task_id}` | 200 | 更新标题、优先级、进度、重试/超时或状态 |
| POST | `/tasks/{task_id}/status` | 200 | 只更新状态 |
| POST | `/tasks/{task_id}/cancel` | 200 | 经 TaskManager 取消 |
| POST | `/tasks/{task_id}/retry` | 200 | 经 TaskManager 重试 |
| DELETE | `/tasks/{task_id}` | 204 | 必要时先取消，再删除任务 |

TaskOut 主要字段：

| 字段 | 含义 |
| --- | --- |
| `id`, `goal_id`, `parent_id` | 任务与目标/父任务关系 |
| `kind`, `title`, `input_json` | 类型、标题和结构化输入 |
| `status`, `priority`, `progress` | 状态、0–100 优先级、0–100 进度 |
| `attempts`, `max_attempts` | 已尝试次数与上限 |
| `timeout_seconds` | 单任务超时，5–86400 秒 |
| `idempotency_key`, `idempotent` | 幂等信息 |
| `lease_until`, `heartbeat_at` | 调度租约与心跳 |
| `result_json`, `error` | 结构化结果与错误 |
| `started_at`, `finished_at`, `created_at`, `updated_at` | 生命周期时间 |

PATCH 示例：

```json
{
  "priority": 80,
  "progress": 40,
  "max_attempts": 3,
  "timeout_seconds": 600
}
```

专用状态接口允许 `queued`、`paused`、`completed`、`failed`、`cancelled`。运行中的任务应使用 `/cancel`，直接改写 running 状态会返回 409。重新置为 queued 会清理旧租约、心跳、开始/结束时间和错误。

## 6. 记忆

资源前缀：`/memories`，全部需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/memories?kind=&archived=&pinned=&q=&limit=100&offset=0` | 200 | 记忆列表，置顶优先、再按更新时间倒序 |
| POST | `/memories` | 201 | 创建记忆 |
| GET | `/memories/{memory_id}` | 200 | 记忆详情 |
| PATCH | `/memories/{memory_id}` | 200 | 局部更新 |
| DELETE | `/memories/{memory_id}` | 204 | 删除记忆 |

`q` 最长 500 字符，对内容做转义后的 LIKE 包含检索。`kind`、`archived`、`pinned` 可独立组合过滤。字段契约：

```json
{
  "kind": "candidate",
  "content": "用户偏好简洁的中文周报",
  "tags": ["偏好", "周报"],
  "pinned": false,
  "archived": false,
  "confidence": 1.0,
  "source_message_id": null
}
```

`content` 最长 200000 字符，`tags` 最多 50 项，`confidence` 为 0–1。`source_message_id` 若存在，必须是当前用户的消息，否则返回 404。Agent 上下文检索与这些管理接口使用同一持久记录。

## 7. 持久日程

资源前缀：`/schedules`，全部需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/schedules?enabled=true&limit=100&offset=0` | 200 | 按 `next_run_at` 升序列出日程 |
| POST | `/schedules` | 201 | 校验并创建日程 |
| GET | `/schedules/{schedule_id}` | 200 | 日程详情 |
| PATCH | `/schedules/{schedule_id}` | 200 | 局部更新并按需重算下次运行 |
| DELETE | `/schedules/{schedule_id}` | 204 | 删除日程 |

```json
{
  "title": "每日晨报",
  "prompt": "总结今天的重要日程与未完成任务",
  "cron": "0 8 * * *",
  "timezone": "Asia/Shanghai",
  "enabled": true,
  "next_run_at": null
}
```

`cron` 使用 croniter 支持的表达式，`timezone` 使用 IANA 名称；无效值返回 422。创建时省略 `next_run_at` 会按当前时间自动计算；修改 cron 或 timezone 会自动重算。显式传入带时区的 `next_run_at` 会转换为 UTC naive 存储，响应还包含 `last_run_at` 与生命周期时间戳。禁用日程不会删除历史任务。

## 8. 审批

资源前缀：`/approvals`，全部需要 Bearer。

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/approvals?status=pending&limit=100&offset=0` | 200 | 审批列表，创建时间倒序 |
| GET | `/approvals/{approval_id}` | 200 | 审批详情 |
| POST | `/approvals/{approval_id}/approve` | 200 | 批准并恢复关联任务 |
| POST | `/approvals/{approval_id}/reject` | 200 | 驳回并恢复关联任务处理 |

审批字段包括 `task_id`、`action`、`payload_json`、`status`、`expires_at` 与 `decided_at`。决定 body 可完全省略，或为：

```json
{"reason":"确认执行该动作"}
```

`reason` 最长 10000 字符，保存到 `payload_json.decision_reason`。仅 pending 可决策；重复决定返回 409。已过期审批会先置为 expired 再返回 409。决定提交后调用 `TaskManager.resume_after_approval(task_id)`，响应同时包含 `approval` 和关联 `task`，客户端应一起刷新两者。

## 9. 设置、连接器、活动与实时事件

### 用户设置

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/settings` | 200 | 按 key 列出当前用户设置 |
| POST | `/settings` | 201 | 创建设置，重复 key 返回 409 |
| GET | `/settings/test-connection` | 200 | 使用当前 MiMo 配置执行最小连接测试，返回 `success/message` |
| GET | `/settings/{key}` | 200 | 读取单个设置 |
| PUT | `/settings/{key}` | 200 | 创建或完整替换值（upsert） |
| DELETE | `/settings/{key}` | 204 | 删除设置 |

创建 body 为 `key`、`value`、`is_secret`；PUT body 为 `value`、`is_secret`。`value` 可以是任意 JSON 值。`is_secret=true` 或 key 被识别为敏感名称时，响应把值替换为 `********`。PUT 对现有设置原样提交该掩码会保留已存秘密；创建新秘密时应发送真实新值。

### 外部连接器

| 方法 | 路径 | 成功状态 | 说明 |
| --- | --- | --- | --- |
| GET | `/connections?kind=caldav&enabled=true&limit=100&offset=0` | 200 | 按类型/启用状态过滤 |
| POST | `/connections` | 201 | 创建连接器 |
| GET | `/connections/{connection_id}` | 200 | 连接器详情 |
| PATCH | `/connections/{connection_id}` | 200 | 局部更新 |
| DELETE | `/connections/{connection_id}` | 204 | 删除连接器 |

```json
{
  "kind": "caldav",
  "name": "个人日历",
  "config": {"url":"https://...","username":"...","password":"..."},
  "enabled": true
}
```

API 字段 `config` 映射数据库 `config_json`。`password`、`token`、`secret`、`api_key`、`private_key` 等敏感键会递归脱敏。PATCH 中保留递归掩码会恢复对应原值，因此可以只改 URL 而不重写密码。常见 `caldav`、`imap`、`rss` 类型由连接器层处理。

### 活动与产物

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/activities?type=&task_id=&goal_id=&after=0&limit=100&offset=0` | 只读 TaskEvent 时间线，ID 倒序；`after` 只保留大于该 ID 的事件 |
| GET | `/activities/artifacts?task_id=&limit=100&offset=0` | 当前用户产物列表，可按任务过滤 |
| GET | `/activities/artifacts/{artifact_id}/download` | 经 Server 鉴权并代理下载产物 |
| GET | `/activities/{activity_id}` | 单个持久事件 |

Activity 字段为 `id`、`goal_id`、`task_id`、`type`、`payload_json`、`created_at`；Artifact 还包含名称、路径、媒体类型和大小。指定不存在或不属于当前用户的 task_id 会返回 404。

### 实时事件 WebSocket

事件流提供 `WS /ws/events` 与 `WS /api/v1/ws/events` 两个同构路径。查询参数 `token=<access_token>` 必填（至少 10 字符），`after=<activity_id>` 默认 0。服务端校验 JWT 与 User，无效身份以 WebSocket 代码 4401 关闭，缺少服务配置以 1011 关闭。

连接接受后先按 ID 升序补发 `id > after` 的持久事件，每批最多 200 条，并通过有界 broker 接收即时通知；持久 live 通知会触发数据库补偿，避免并发提交导致重复或乱序。事件 JSON 统一为：

```json
{
  "id": 123,
  "user_id": "用户UUID",
  "goal_id": "目标UUID或null",
  "task_id": "任务UUID或null",
  "type": "message.delta",
  "payload": {"delta":"文本增量"},
  "created_at": "2026-01-01T00:00:00+00:00"
}
```

持久事件的 `id` 为正整数；瞬态事件（例如 `message.delta`）的 `id=null`，只在线投递、不推进持久游标。每 20 秒发送 `{"type":"ping","timestamp":"...","after":last_id}` 心跳；客户端帧不承载命令。断线重连时传最后连续处理的持久 ID；客户端应分别处理瞬态增量与持久事件，并按持久 ID 去重，也可用活动 REST API 补拉。

## 10. 语音 HTTP 与 WebSocket

- `POST /audio/asr`：base64 WAV/MP3 转写。
- `POST /audio/tts`：流式 24 kHz 单声道 PCM16。
- `WS /ws/voice`（Android 稳定入口）：控制 JSON + 自定义二进制帧。
- `WS /api/v1/ws/voice` 与 `WS /api/v1/voice/ws`：版本化别名。

完整请求字段、帧头、事件和打断语义见 [VOICE.md](VOICE.md)。

## 11. 内部 Executor API

Executor 是独立服务，不是 Android 的公开入口。默认地址仅 Compose 内可达：`http://executor:8081`。所有内部业务接口使用：

```text
Authorization: Bearer <EXECUTOR_TOKEN>
```

Compose 从根 `.env` 的 `AGENT_EXECUTOR_TOKEN` 映射该值。Server 的 `ExecutorClient` 使用同一秘密。

### 健康检查

`GET /healthz`，无需 Bearer：

```json
{"status":"ok"}
```

### 执行工具

`POST /api/v1/execute`

```json
{
  "task_id": "task-01",
  "tool": "workspace_write",
  "arguments": {"path":"notes/result.txt","content":"执行结果"},
  "timeout_seconds": 30
}
```

`task_id` 必填，匹配 `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`；`tool` 必填，`arguments` 默认 `{}`，`timeout_seconds` 必须大于 0，并受 executor 最大超时限制。

当前工具：

| `tool` | 主要 `arguments` | 用途 |
| --- | --- | --- |
| `workspace_list` | `path="."`、`recursive=false`、`limit` | 列出任务工作区；limit 受 `MAX_LIST_ENTRIES` 限制 |
| `workspace_read` | `path`、`encoding="utf-8"` | 读取文本，大小受 `MAX_FILE_BYTES` 限制 |
| `workspace_write` | `path`、`content`、可选 `encoding` | 原子写入文本并登记产物 |
| `workspace_delete` | `path`、`recursive=false` | 删除文件/目录；非空目录需 recursive |
| `shell` | `argv` 或 `command`（二选一）、`cwd="."` | 直接启动进程，不经过 shell；stdout/stderr 有字节上限 |
| `browser` | `action=open/read/click/fill/screenshot`，配合 `url/selector/value/path` 等 | 可选 Playwright/Chromium，按 task 隔离浏览器 context |

所有工作区路径使用相对正斜杠路径；绝对路径、`..`、反斜杠和越界符号链接会被拒绝。Shell 非零退出码或超时会以 `ok=false` 返回，同时保留退出码、截断标记与受限输出。Browser screenshot 把图像写入任务工作区并加入 `artifacts`。Browser `click/fill` 首次返回 `approval_required=true`；Server 在用户批准后携带 `approved=true` 重放该检查点。

响应为 `ToolResult`：

```json
{
  "task_id": "task-01",
  "tool": "workspace_write",
  "ok": true,
  "data": {"path":"notes/result.txt"},
  "error": null,
  "artifacts": ["notes/result.txt"],
  "duration_ms": 12,
  "approval_required": false
}
```

工具业务失败通常仍在 HTTP 200 的 `ToolResult` 中体现为 `ok=false` 与 `error.code/message/details`；调用方必须检查 `ok`。缺少/错误 Bearer 返回 401，schema 错误返回 422。

### 下载产物

`GET /api/v1/artifacts/{task_id}/{path}`，需要同一个内部 Bearer。产物路径由 executor 按 task 工作区解析，响应带附件 Content-Disposition 和推断的媒体类型。

## 12. 状态码与客户端重试

| 状态 | 常见含义 | 客户端行为 |
| --- | --- | --- |
| 200 | 查询、更新或内部工具执行完成 | 检查业务字段；executor 还需检查 `ok` |
| 201 | 会话、目标等资源已创建 | 保存响应 ID |
| 202 | 消息已保存，异步任务已接受 | 跟踪 task，而非当作最终回答 |
| 204 | 删除/登出成功 | 不解析响应体 |
| 401 | access/refresh 无效或缺失 | REST access 过期时旋转 refresh；失败后重新登录 |
| 404 | 资源不存在或不属于当前用户 | 不跨用户重试相同 ID |
| 409 | 当前状态不允许操作 | 先刷新状态，如运行任务改用 cancel |
| 422 | 请求字段或 JSON 校验失败 | 按 `detail[].loc` 修正字段 |
| 429 | 上游或服务限流 | 遵循 Retry-After，指数退避 |
| 502/503 | 上游 Provider 错误或服务未配置 | 检查 MiMo/密钥/网络，保留任务以便重试 |

对创建目标的网络重试应复用相同 `idempotency_key`。Refresh 请求具有旋转副作用，客户端应串行化刷新并持久化最新 token，避免多个并发请求把彼此的新 refresh token 立即变成旧值。
