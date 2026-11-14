# Personal Agent Flutter App

独立的 Material 3 中文 Flutter 客户端。工程是原生 Flutter/Android 结构，不依赖 `build_runner`、Freezed、JSON 生成器或其他代码生成步骤。

## 功能

- 服务器 Base URL 配对、登录、单航班令牌刷新、旋转令牌原子保存、服务端登出与本地安全清理。
- 持久会话历史、主对话流式消息、独立 Goal 创建。
- 语音开始/结束/取消/打断、PCM16 录音帧、24 kHz 服务端 TTS 帧播放、旧回合隔离。
- Goal 远程列表与按用户本地缓存；Task 刷新、取消、重试。
- 审批批准/拒绝及关联任务刷新、活动时间线。
- 记忆新增、读取、编辑、删除。
- cron 日程新增、读取、启停、删除，支持 IANA 时区。
- 服务端设置、连接测试、本地语音/通知偏好、浅色/深色/系统主题。
- 宽屏 `NavigationRail` 与窄屏 `NavigationBar` 响应式导航。

## 环境

- Flutter stable，最低 3.47.0；本次验证使用 Flutter 3.50.0 / Dart 3.14.0。
- Dart 最低 3.13.0（随 Flutter 提供）。最低版本与 `pubspec.lock` 的 SDK 约束一致。
- Android SDK、JDK 17+；本次构建使用 JDK 21，Android `minSdk = 24`、`targetSdk = 36`。
- 当前源码包含 Android 平台壳。业务 Dart 代码集中在平台无关层，其他平台可用 Flutter 工具另行添加平台壳。

## 构建与检查

在本目录执行：

```bash
flutter pub get
flutter analyze
flutter test
flutter run
```

无 Flutter/Dart SDK 的开发机可先运行仓库自带结构检查：

```powershell
pwsh -File tool/check_structure.ps1
```

结构检查覆盖必需文件、Dart 相对/包内导入目标、Android XML、必需依赖和 Gradle Wrapper JAR 签名。

构建 APK：

```bash
flutter build apk --debug
flutter build apk --release
```

中国网络环境下若 Maven 依赖握手失败，可在已执行 `flutter pub get` 后使用可选镜像初始化脚本：

```powershell
cd android
.\gradlew.bat assembleRelease -I ..\tool\mirrors.init.gradle
```

脚本优先使用华为云/阿里云 Maven 镜像，并保留官方仓库回退；同时将旧音频插件的 Android compileSdk 统一为 36。项目已采用 Gradle 9.5、AGP 9.3.1、Kotlin 2.4.20，并在 `gradle.properties` 关闭 Kotlin 增量缓存以避免 Windows 文件锁冲突。

标准 Gradle Wrapper 位于 `android/`。Flutter 命令会生成本机 `android/local.properties`；直接调用 Gradle 前，可在该文件设置：

```properties
flutter.sdk=F:\\flutter
sdk.dir=C:\\Users\\YOUR_NAME\\AppData\\Local\\Android\\Sdk
```

`release` 构建默认沿用模板的 debug 签名配置，发布前应在 `android/app/build.gradle.kts` 配置自己的签名。

## 服务器配置

默认地址：`http://10.0.2.2:8080/api/v1`，适合 Android 模拟器访问宿主机。实体设备填写服务器局域网地址；HTTPS 自动转换为 WSS。

REST 使用 `Authorization: Bearer ACCESS_TOKEN`。普通请求遇到 401 时通过独立 Dio 和共享刷新 Future 串行刷新并重试一次，避免 refresh token 并发旋转。访问/刷新令牌作为一条 JSON 记录原子写入 `flutter_secure_storage`；Base URL、用户名、按用户隔离的目标缓存和界面偏好存入 `shared_preferences`。退出或换账号会重置业务 Provider 和语音状态。

| 功能 | 方法与路径 |
| --- | --- |
| 登录 | `POST /auth/login`，body `{username,password}` |
| 刷新 | `POST /auth/refresh`，body `{refresh_token}` |
| 登出 | `POST /auth/logout`，body `{refresh_token}` |
| 会话 | `GET/POST /conversations`；`GET /conversations/{id}/messages` |
| 发送消息 | `POST /conversations/{id}/messages`，body `{content}` |
| 目标 | `GET /goals`；`POST /goals`，body `{title,input,idempotency_key}` |
| 任务 | `GET /tasks`；`POST /tasks/{id}/cancel`；`POST /tasks/{id}/retry` |
| 审批 | `GET /approvals`；`POST /approvals/{id}/approve`；`POST /approvals/{id}/reject` |
| 活动 | `GET /activities` |
| 记忆 CRUD | `GET/POST /memories`；`PATCH/DELETE /memories/{id}` |
| 日程 CRUD | `GET/POST /schedules`；`PATCH/DELETE /schedules/{id}` |
| 设置 | `GET /settings`；`GET /settings/test-connection` |
| 事件 | `WSS /ws/events?token=ACCESS_TOKEN&after=ACTIVITY_ID` |
| 语音 | `WSS /ws/voice?token=ACCESS_TOKEN` |

日程创建字段为 `{title,prompt,cron,timezone,enabled}`；省略 `next_run_at` 时由服务端计算。设置列表支持服务端直接返回 `SettingOut[]`。其他列表解析支持直接数组以及 `{items:[]}`、`{data:[]}`、`{results:[]}`；实体解析支持直接对象、`{data:{...}}` 和对应命名单层包装。

### 事件流

事件 JSON 使用顶层 `id/type/goal_id/task_id/conversation_id` 与 `payload` 对象。`ServerEvent` 分离 envelope 和业务数据；持久 ID 用于去重和断线后的 `after` 游标，`id=null` 的瞬态事件不推进游标。连接握手成功后才标记在线，4401 会刷新令牌再重连。

对话处理 `message.delta`、`chat.delta`、`response.delta`，以及 `message.completed`、`message.done`、`chat.completed`、`response.completed`、`agent.completed`。以消息/任务 ID 聚合流式气泡。稀疏 `goal.*`、`task.*`、`approval.*`、`memory.*`、`schedule.*` 事件触发完整资源重同步，并进入活动时间线；`ping` 不进入活动列表。

### 语音协议

开始控制同时携带客户端生成的 `turn_id`（`kind` 与 `type` 均可被服务端识别）：

```json
{"kind":"start","type":"start","turn_id":"turn-01","format":"pcm16","sample_rate":16000}
```

双向 PCM 音频使用以下二进制布局：

```text
[4 字节无符号大端 JSON 头长度]
[UTF-8 JSON：{"kind":"audio","turn_id":"...","seq":0}]
[PCM16 单声道 payload]
```

控制消息为 `end`、`cancel`、`interrupt`，附带 `turn_id`。客户端处理服务端 `started/state/transcript/text/sentence/done/interrupted/error` 事件；输出音频帧头的 `sample_rate`（通常 24000）和 `channels` 用于播放器配置。音频按 `seq` 串行送入播放器，取消/打断会使旧回合及已排队旧帧失效。

`VoiceTransport`、`AudioCapture`、`AudioPlayer`、`MicrophonePermissionGateway` 均为独立接口：

- `WebSocketVoiceTransport`：协议实现。
- `RecordAudioCapture`：`record` 插件适配器。
- `FlutterPcmSoundAudioPlayer`：`flutter_pcm_sound` 适配器；插件调用集中在 `lib/features/voice/data/audio_io.dart`。
- `InMemoryVoiceTransport`、内存录音/播放器与文件录音/播放器假实现：集中在 `lib/features/voice/data/fake_audio.dart`，用于单元测试和 widget test。

## 目录

```text
lib/
  main.dart
  app.dart
  app/                 # Provider 组合、实时事件、响应式导航
  config/              # Base URL 与 WebSocket 地址
  core/                # Dio、安全存储、本地缓存、工具
  shared/              # 手写 JSON 模型、通用状态与组件
  features/
    auth/              # data / application / presentation
    chat/              # 会话 REST、流式聚合与对话 UI
    voice/             # 协议、音频适配器、状态机与按钮
    goals/
    approvals/
    activity/
    memories/
    schedule/
    settings/
android/               # Android 壳、Manifest、资源、Gradle Wrapper
test/                  # 协议、配置、控制器和 widget 测试
tool/                  # 无 SDK 静态结构检查
```

## 本次验证

- `flutter analyze --no-pub`：**No issues found**。
- `flutter test --no-pub`：**10 项测试全部通过**。
- `dart format lib test`：已格式化 52 个 Dart 文件。
- `tool/check_structure.ps1`：**PASS**；52 个 Dart 文件、181 个本地导入、8 个 Android XML、19 个必需文件检查通过。
- APK 构建：**Release 成功**。输出 `build/app/outputs/flutter-apk/app-release.apk`，约 55.1 MB；包名 `com.example.personal_agent`，`minSdk=24`、`targetSdk=36`，已通过 `apksigner verify`。
- APK SHA-256：`57de9eaa60fe6a862163041530c71257d3c95968c946dc1d7d3b9ecb9d21fdfb`。当前使用 Android Debug 证书，适合直接侧载测试；正式发布时替换为自有签名。
- 真机麦克风、扬声器、实际后端登录及 WSS 联调：未执行，需要运行中的服务端和 Android 设备。

Flutter 3.47+ 将 Material 迁移到独立包；本工程使用 Material 3 兼容层，并在 `analysis_options.yaml` 中针对该兼容策略关闭 `deprecated_member_use` 提示。
