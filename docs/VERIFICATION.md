# 验证记录

验证时间：2026-11-15（Asia/Shanghai）。Provider 合同测试使用 `httpx.MockTransport`；真实 MiMo 联调单独执行，不输出密钥。

## Server / Executor

```powershell
.\.venv\Scripts\ruff.exe check server executor
.\.venv\Scripts\python.exe -m mypy server\personal_agent --ignore-missing-imports
.\.venv\Scripts\python.exe -m pytest
```

结果：

- Ruff：全部通过。
- mypy：46 个 Server 源文件无类型错误。
- pytest：**96 passed, 1 skipped**。
- 跳过项为 Windows 下的 POSIX 权限位专项；Linux 容器执行该路径。
- 并发压力用例真实执行 10 个 Goal、30 个 Task，最大并发工作槽为 4，全部成功。
- Alembic 新库升级、FTS5 表创建以及 `alembic check` 模型一致性均通过。
- MiniMax Global/CN、OpenAI 工具调用、multipart ASR、PCM TTS、业务限流及 MiMo/MiniMax 混合 Provider 均有合同测试。
- 一键部署器通过无人值守 MiMo/MiniMax/混合配置、密钥脱敏和缺参测试；Watchdog 通过失败阈值、恢复、冷却及重启预算测试。

## 真实 MiMo 全链路

使用有效 Token Plan 配置实际执行 `python scripts/verify_mimo_live.py`：LLM 返回成功，TTS 返回 **138,240 字节 PCM**，将该音频封装为 WAV 后调用 ASR 成功返回中文识别文本。验证过程不打印或提交 API Key。

真实 Uvicorn 双进程联调：

```powershell
python scripts/smoke_http.py --password '管理员密码' --internal-token '内部令牌'
```

已验证 Server/Executor 健康检查、管理员登录、访问令牌、目标列表鉴权、匿名 401、工作区写入与产物下载。联调临时服务及数据已清理。

## Flutter

验证环境：Flutter 3.50.0 stable / Dart 3.14.0。

```sh
flutter analyze --no-pub
flutter test --no-pub
```

结果：静态分析 **No issues found**；**10/10** 测试通过。覆盖认证状态、REST/WSS 地址、事件 envelope/payload 和持久游标、PCM 帧编解码、语音回合与打断、登录组件及语音按钮。

### Android Release APK

- 实际 Gradle `assembleRelease`：**BUILD SUCCESSFUL**（346 个任务）。
- 输出：`app/build/app/outputs/flutter-apk/app-release.apk`。
- 大小：55,057,301 字节；包名 `com.example.personal_agent`；`minSdk=24`、`targetSdk=36`。
- `apksigner verify --print-certs` 通过；当前证书为 Android Debug，供侧载测试。
- SHA-256：`57de9eaa60fe6a862163041530c71257d3c95968c946dc1d7d3b9ecb9d21fdfb`。

## 边界

- MiMo 真实全链路已通过；MiniMax Global/CN 在线账号验收需部署时填入对应 API Key。
- 当前开发机没有 Docker daemon；Compose/YAML、Dockerfile、ASGI 路径与脚本已静态检查，多架构镜像由 `.github/workflows/ci.yml` 的 Buildx 任务构建。
- Android 真机麦克风、扬声器及后台行为需要连接实体设备验收；模拟器/桌面假实现已覆盖协议与状态机。
