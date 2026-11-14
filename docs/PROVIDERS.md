# AI Provider：MiMo 与 MiniMax

Server 使用统一的 Provider 契约驱动 Agent、后台任务、HTTP ASR/TTS 和 WebSocket 语音对话。既可整套选择 MiMo/MiniMax，也可分别指定 LLM、ASR、TTS；App 无需修改。

## 默认：Xiaomi MiMo

```dotenv
AGENT_AI_PROVIDER=mimo
AGENT_MIMO_API_KEY=你的MiMo密钥
AGENT_MIMO_LLM_MODEL=mimo-v2.6-flash
AGENT_MIMO_ASR_MODEL=mimo-v2.5-asr
AGENT_MIMO_TTS_MODEL=mimo-v2.5-tts
```

- 文本与工具调用：`POST https://api.xiaomimimo.com/v1/chat/completions`
- ASR：同一 Chat Completions 端点的 `input_audio` Base64 WAV/MP3。
- TTS：SSE 流式 PCM16。
- 官方文档：[模型列表](https://mimo.mi.com/docs/zh-CN/quick-start/summary/model)、[ASR](https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition)、[TTS](https://mimo.mi.com/docs/zh-CN/api/audio/tts)。

## MiniMax

```dotenv
AGENT_AI_PROVIDER=minimax
AGENT_MINIMAX_API_KEY=你的MiniMax密钥
AGENT_MINIMAX_BASE_URL=https://api.minimax.io/v1
AGENT_MINIMAX_LLM_MODEL=MiniMax-M3.1-Flash
AGENT_MINIMAX_ASR_MODEL=asr-1.0
AGENT_MINIMAX_TTS_MODEL=speech-2.8-turbo
AGENT_MINIMAX_VOICE=Chinese (Mandarin)_Lyrical_Voice
AGENT_MINIMAX_REASONING_EFFORT=medium
```

### MiniMax 中国区（CN）

```dotenv
AGENT_AI_PROVIDER=minimax
AGENT_MINIMAX_REGION=cn
AGENT_MINIMAX_API_KEY=你的中国区MiniMax密钥
```

CN 模式将 LLM、ASR、TTS 统一切换到 `https://api.minimax.cn/v1`，路径仍为 `/chat/completions`、`/speech_to_text`、`/t2a_v2`。一键部署可使用 `python scripts/deploy.py --provider minimax --minimax-region cn --yes`。自定义网关设置 `AGENT_MINIMAX_BASE_URL` 后优先保留该地址。

### 能力映射

| 功能 | MiniMax 实现 |
| --- | --- |
| Agent 文本与函数调用 | OpenAI 兼容 `POST /v1/chat/completions` |
| 流式文本 | SSE `delta.content`；`reasoning_content` 独立且不混入回答 |
| 语音识别 | `POST /v1/speech_to_text`，multipart 文件，`asr-1.0` |
| 语音合成 | `POST /v1/t2a_v2`，默认 `speech-2.8-turbo` |
| App 音频 | 24 kHz、单声道 PCM，十六进制音频块在 Server 转为二进制帧 |
| 打断与恢复 | 复用 VoiceSession 的 turn/generation 取消机制 |

`AGENT_MINIMAX_REASONING_EFFORT` 支持 `low/medium/high/xhigh/max`；实时语音建议 `low` 或 `medium`。TTS 追求更高音质时切换为 `speech-2.8-hd`。

MiniMax ASR 官方限制为单文件 50 MB、500 秒；本项目 WAV/MP3 输入先经过 Base64 解码和大小检查。原始 PCM 会先封装为 WAV。TTS 请求使用 `exclude_aggregated_audio=true`，并兼容旧网关的最终聚合包，避免重复播放。

官方文档：[Chat Completions](https://platform.minimax.io/docs/api-reference/text-chat-openai)、[Speech-to-Text](https://platform.minimax.io/docs/api-reference/speech-to-text)、[TTS HTTP](https://platform.minimax.io/docs/api-reference/speech-t2a-http)、[模型列表](https://platform.minimax.io/docs/faq/models)。中国区参考：[CN Chat Completions](https://platform.minimax.cn/docs/api-reference/text-chat-openai)、[CN ASR](https://platform.minimax.cn/docs/api-reference/speech-to-text)、[CN TTS](https://platform.minimax.cn/docs/api-reference/speech-t2a-http)。

## 混合 Provider：MiniMax 大模型 + 小米 ASR

以下配置让 MiniMax 只负责 LLM，小米 MiMo 负责语音识别和语音合成：

```dotenv
AGENT_AI_PROVIDER=mimo
AGENT_LLM_PROVIDER=minimax
AGENT_ASR_PROVIDER=mimo
AGENT_TTS_PROVIDER=mimo
AGENT_MINIMAX_API_KEY=你的MiniMax密钥
AGENT_MIMO_API_KEY=你的MiMo密钥
```

三个覆盖项留空或注释时继承 `AGENT_AI_PROVIDER`。`AGENT_LLM_PROVIDER`、`AGENT_ASR_PROVIDER`、`AGENT_TTS_PROVIDER` 可分别设为 `mimo` 或 `minimax`。例如 MiniMax LLM + MiMo ASR + MiniMax TTS 只需把最后一项改为 `minimax`。

内部由 `HybridProvider` 路由调用并复用同一 Provider 实例；连接测试会报告组合名称。组合中缺少任一所需密钥时，会列出对应的 `AGENT_MINIMAX_API_KEY` 或 `AGENT_MIMO_API_KEY`。

## 连接测试与排障

1. 修改 Provider 后重启：`docker compose up -d`。
2. 在 App「设置 → 测试连接」执行一次最小文本请求；返回信息会显示 MiMo 或 MiniMax 及模型名。
3. 分别调用 HTTP `/audio/asr`、`/audio/tts`，再测试 WebSocket 语音回合。
4. `429` 按 Retry-After/指数退避重试；MiniMax `base_resp.status_code=1002/1039` 会映射为限流错误。
5. 两套密钥可以同时保留在 `.env`；单一 Provider 模式只实例化选中项，混合模式按能力复用实例并仅调用实际需要的上游。
