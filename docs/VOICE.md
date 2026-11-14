# 实时语音协议

语音服务由 `server/personal_agent/services/voice.py` 的 `VoiceSession` 实现，WebSocket 适配器位于 `api/voice.py`，独立 ASR/TTS HTTP 接口位于 `api/audio.py`。默认提供方为 MiMo 云，也可通过 `AGENT_AI_PROVIDER=minimax` 使用 MiniMax；两套实现均输出 App 所需的 24 kHz PCM 帧。

## 1. 地址与认证

`register_voice_routes()` 默认挂载两个 `/api/v1` 版本化路径，并为 Android 额外注册根路径别名：

- `ws(s)://NAS_HOST:PORT/ws/voice`（Android 稳定入口）
- `ws(s)://NAS_HOST:PORT/api/v1/ws/voice`
- `ws(s)://NAS_HOST:PORT/api/v1/voice/ws`

查询参数：

| 参数 | 说明 |
| --- | --- |
| `token` | access token；Android 使用此查询参数，也可改用 `Authorization: Bearer <token>` 请求头。配置 `AGENT_SECRET_KEY` 后令牌必填，缺失或无效时以 WebSocket 1008 关闭 |
| `conversation_id` | 绑定已有会话；VoiceSession 会校验该会话属于当前用户。省略时，在启用数据库会话工厂的情况下自动创建会话 |

Android 的 `AppConfig.voiceUri()` 使用根路径 `/ws/voice?token=...`；`register_voice_routes()` 在挂载 `/api/v1` 别名时会自动额外注册这个根路径。不要信任客户端自行传入的 `user_id` 作为身份认证；它只应作为开发测试回退。

WebSocket 建立后有两种消息：

1. **JSON 文本消息**：start/end/interrupt/cancel 控制，以及服务端状态与文本事件。
2. **二进制消息**：带 JSON 帧头的输入/输出音频。

## 2. 二进制帧格式

```text
0               4               4 + N                         帧结束
┌───────────────┬───────────────┬─────────────────────────────┐
│ header_len    │ UTF-8 JSON    │ 原始二进制 payload           │
│ uint32 大端   │ N 字节        │ 剩余全部字节                 │
└───────────────┴───────────────┴─────────────────────────────┘
```

等价于 Python：

```python
frame = struct.pack("!I", len(header_json)) + header_json + payload
```

约束：

- `header_len` 是 JSON 头的字节数，不包含前四字节，也不包含 payload。
- 最大 JSON 头为 `64 * 1024` 字节；空 JSON 头、截断头或非 UTF-8 JSON 会返回 error。
- JSON 必须是对象，`kind` 为非空字符串，`turn_id` 为字符串或 `null`，`seq` 为非负整数。
- 一个 WebSocket binary message 对应一个完整帧；不要把四字节长度理解为整个 WebSocket 消息长度。

输入音频头示例：

```json
{"kind":"audio","turn_id":"turn-01","seq":0}
```

输入 `kind` 接受 `audio`、`pcm`、`pcm16` 或 `input_audio`。同一 turn 的 `seq` 应从 0 递增；payload 按到达顺序追加到该回合的音频缓冲区。

## 3. 客户端控制消息

控制消息是 WebSocket JSON 文本，不使用上述二进制帧封装。命令字段可写 `kind` 或 `type`。

### start

```json
{
  "kind": "start",
  "turn_id": "turn-01",
  "conversation_id": "可选会话UUID",
  "format": "pcm16",
  "sample_rate": 16000
}
```

- `turn_id` 可省略，服务端生成 UUID；建议客户端生成以便打断和去重。
- `format` 接受 `pcm16`（别名 `pcm`）、`wav`、`mp3`，默认 `pcm16`。
- `sample_rate` 默认 `16000`，用于把裸 PCM16 封装成 WAV 后交给 ASR。
- WAV/MP3 输入应发送完整编码数据；可以拆成多个二进制消息，服务会先按回合拼接。
- start 会取消旧活动回合并开始新回合，这是实现 barge-in 的方式之一。

成功时先收到：

```json
{"kind":"started","turn_id":"turn-01"}
{"kind":"state","state":"listening","turn_id":"turn-01"}
```

### end

```json
{"kind":"end","turn_id":"turn-01"}
```

结束收音并启动异步 ASR → LLM → TTS。空音频会返回 `{"kind":"error","error":"Empty audio turn",...}`。

### interrupt / cancel

```json
{"kind":"interrupt","turn_id":"turn-01"}
```

`cancel` 是同义命令。它取消指定活动回合的收音、ASR、生成或 TTS，清理回合缓冲区，并发送 `interrupted`。不带 `turn_id` 时中断当前活动回合；不匹配的 turn 不影响新回合。

## 4. 状态机与回合隔离

```text
idle → listening → transcribing → thinking → speaking → idle
  ↑                                                   │
  └────────────── interrupt / cancel ─────────────────┘
```

状态可能因流式文本和分句 TTS 在 `thinking` / `speaking` 间推进，客户端应以收到的事件为准。内部 `generation` 与 `turn_id` 同时校验；旧回合晚到的 ASR、文本或音频不会再进入新回合。

| 状态 | 含义 |
| --- | --- |
| `idle` | 可开始新回合 |
| `listening` | 接收并缓冲输入音频 |
| `transcribing` | MiMo ASR 转写 |
| `thinking` | MiMo LLM 流式生成 |
| `speaking` | 按句调用 TTS 并输出 PCM16 |
| `closed` | 会话清理，不再接受新回合 |

## 5. 服务端 JSON 事件

| `kind` | 关键字段 | 含义 |
| --- | --- | --- |
| `started` | `turn_id` | start 已接受 |
| `state` | `state`, `turn_id` | 状态变化 |
| `transcript` | `text`, `turn_id` | 用户语音的 ASR 文本 |
| `text` | `delta`, `turn_id` | 助手文本增量 |
| `sentence` | `index`, `text`, `turn_id` | 一个句子开始送 TTS |
| `done` | `text`, `turn_id` | 回合完成，`text` 为完整助手回答 |
| `interrupted` | `turn_id` | 指定回合已取消 |
| `error` | `error`，可含 `turn_id` | 帧、控制或供应商错误 |

启用数据库时，服务端读取最近 40 条已完成消息，写入用户转写和助手消息；助手消息经历 `streaming` → `completed` / `cancelled` / `error`。断线后可通过普通会话 REST API 重读持久内容。

## 6. 输出音频帧

TTS 输出是二进制帧，不是 JSON 中的 base64。示例头：

```json
{
  "kind": "audio",
  "turn_id": "turn-01",
  "seq": 3,
  "sentence_index": 1,
  "format": "pcm16",
  "sample_rate": 24000,
  "channels": 1
}
```

payload 是原始 PCM16 音频，按 `seq` 顺序播放。`sentence_index` 标识句子；输出采样率固定为 24000 Hz、单声道。客户端打断时应同时停止本地播放队列并丢弃被中断 turn 的后续帧。

输入 PCM16 与输出 PCM16 的采样率不同：默认输入 16000 Hz，输出 24000 Hz，不要复用同一个播放器采样率常量。

## 7. HTTP ASR / TTS 调试接口

这些接口挂载在 `/api/v1`，适合单独验证 MiMo 配置，不要求先建立语音 WebSocket。

### ASR

`POST /api/v1/audio/asr`

```json
{
  "audio_base64": "<完整WAV或MP3的base64>",
  "audio_format": "wav",
  "language": "auto"
}
```

- `audio_base64` 也接受字段别名 `audio`、`data`；可带 base64 data URL 前缀。
- `audio_format` 仅接受 `wav` / `mp3`，别名 `format`。
- `language` 接受 `auto` / `zh` / `en`。
- 响应：`{"text":"识别结果"}`。
- 隐藏别名：`POST /api/v1/audio/transcribe`。

### TTS

`POST /api/v1/audio/tts`

```json
{
  "text": "你好，这是语音合成测试。",
  "voice": "mimo_default",
  "instructions": "使用自然、平稳的语气"
}
```

`voice` 与 `instructions` 可省略。响应是流式 `audio/pcm`，并带：

```text
X-Audio-Format: pcm16
X-Audio-Sample-Rate: 24000
X-Audio-Channels: 1
Cache-Control: no-store
```

隐藏别名：`POST /api/v1/audio/synthesize`。

```sh
curl --fail --request POST http://127.0.0.1:8080/api/v1/audio/tts \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer ACCESS_TOKEN' \
  --data '{"text":"你好，这是语音合成测试。","voice":"mimo_default"}' \
  --output reply.pcm
```

MiMo 429 会映射为 HTTP 429；其他 Provider 协议、传输或上游错误通常映射为 502。TTS 是流式响应，开始传输之后的上游中断可能表现为连接提前结束，客户端还需检查音频帧完整性。

WebSocket 单轮原始录音上限为 16 MiB，超限会中断该 turn 并返回 `error`；HTTP ASR 的 Base64 文本上限为 24 MiB。两类接口都要求用户认证。

## 8. 语音目标与后台 Agent

普通问答留在 VoiceSession 主会话。包含“后台执行、创建任务、创建目标、每天、每周、提醒我、持续监控”等明确目标的识别文本会调用 `TaskManager.create_goal`，立即返回 `task_created` 事件，并播报“已创建后台任务，可以在任务中心查看进度”。

- 后台任务使用独立 Goal/Task、AgentRunner 和 Executor，App 关闭后继续运行。
- 创建幂等键为 `voice:{turn_id}`，重复回合不会重复创建。
- 语音确认不等待后台执行完成；任务进度通过 `/ws/events` 和任务中心查看。
- 客户端 start 可携带 `mode=chat|auto|task`；默认 `auto`，`chat` 禁止自动建任务，`task` 强制把本轮作为后台目标。
- 任务创建后即使语音回合被打断，后台任务仍按独立生命周期继续。

## 9. 最小帧编解码示例

```python
import json
import struct


def encode_audio(payload: bytes, turn_id: str, seq: int) -> bytes:
    header = json.dumps(
        {"kind": "audio", "turn_id": turn_id, "seq": seq},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(header) > 64 * 1024:
        raise ValueError("header too large")
    return struct.pack("!I", len(header)) + header + payload


def decode_audio(frame: bytes) -> tuple[dict, bytes]:
    size = struct.unpack("!I", frame[:4])[0]
    if size == 0 or size > 64 * 1024 or len(frame) < 4 + size:
        raise ValueError("invalid header")
    return json.loads(frame[4:4 + size]), frame[4 + size:]
```

## 9. 联调顺序

1. `GET /healthz`，再检查 `AGENT_MIMO_API_KEY` 与模型配置。
2. 用 HTTP `/audio/tts` 得到非空 PCM 流，验证 24 kHz 单声道播放。
3. 用 HTTP `/audio/asr` 验证一段完整 WAV/MP3。
4. 建立 WebSocket，start → 发送一个带帧头的音频块 → end。
5. 检查 transcript、text、sentence、audio、done 的顺序和相同 `turn_id`。
6. 在 TTS 播放中发送 interrupt，再立即 start 新 turn，确认旧 turn 音频被丢弃。
7. 通过 Android 实际路径再测一次，确认根路径或 `/api/v1` 别名与主应用注册保持一致。
