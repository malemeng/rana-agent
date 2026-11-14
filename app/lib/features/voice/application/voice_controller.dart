import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../config/app_config.dart';
import '../../../core/storage/token_storage.dart';
import '../../../core/utils/errors.dart';
import '../../../core/utils/id.dart';
import '../data/audio_io.dart';
import '../data/voice_transport.dart';

enum VoicePhase { idle, connecting, listening, processing, speaking, error }

class VoiceState {
  const VoiceState({
    this.phase = VoicePhase.idle,
    this.turnId,
    this.sampleRate = AppConfig.voiceSampleRate,
    this.error,
  });

  final VoicePhase phase;
  final String? turnId;
  final int sampleRate;
  final String? error;

  VoiceState copyWith({
    VoicePhase? phase,
    String? turnId,
    int? sampleRate,
    String? error,
    bool clearError = false,
  }) => VoiceState(
    phase: phase ?? this.phase,
    turnId: turnId ?? this.turnId,
    sampleRate: sampleRate ?? this.sampleRate,
    error: clearError ? null : error ?? this.error,
  );
}

class VoiceController extends StateNotifier<VoiceState> {
  VoiceController({
    required VoiceTransport transport,
    required AudioCapture capture,
    required AudioPlayer player,
    required MicrophonePermissionGateway permission,
    required TokenStorage tokenStorage,
    required String Function() baseUrl,
    void Function(String role, String text)? onTranscript,
  }) : _transport = transport,
       _capture = capture,
       _player = player,
       _permission = permission,
       _tokenStorage = tokenStorage,
       _baseUrl = baseUrl,
       _onTranscript = onTranscript,
       super(const VoiceState()) {
    _eventSubscription = _transport.events.listen(_onEvent);
  }

  final VoiceTransport _transport;
  final AudioCapture _capture;
  final AudioPlayer _player;
  final MicrophonePermissionGateway _permission;
  final TokenStorage _tokenStorage;
  final String Function() _baseUrl;
  final void Function(String role, String text)? _onTranscript;
  late final StreamSubscription<VoiceEvent> _eventSubscription;
  Future<void> _playbackQueue = Future<void>.value();
  int _sequence = 0;
  int _lastPlaybackSequence = -1;
  int _generation = 0;
  bool _starting = false;
  String _assistantBuffer = '';

  Future<void> toggle() async {
    switch (state.phase) {
      case VoicePhase.listening:
        await stop();
      case VoicePhase.speaking:
        await interrupt();
      case VoicePhase.connecting:
        return;
      case VoicePhase.processing:
        await cancel();
      case VoicePhase.idle:
      case VoicePhase.error:
        await start();
    }
  }

  Future<void> start() async {
    if (_starting) return;
    _starting = true;
    final turnId = createId('turn');
    final generation = ++_generation;
    _sequence = 0;
    _lastPlaybackSequence = -1;
    _assistantBuffer = '';
    state = VoiceState(phase: VoicePhase.connecting, turnId: turnId);
    try {
      if (!await _permission.ensureGranted()) {
        throw StateError('麦克风权限未授予');
      }
      final token = await _tokenStorage.readAccessToken();
      if (token == null || token.isEmpty) throw StateError('登录状态已失效');
      await _player.stop();
      await _transport.connect(AppConfig.voiceUri(_baseUrl(), token));
      await _transport.start(
        turnId: turnId,
        sampleRate: AppConfig.voiceSampleRate,
      );
      await _capture.start(
        sampleRate: AppConfig.voiceSampleRate,
        onData: _sendAudio,
      );
      if (!mounted || generation != _generation) return;
      state = state.copyWith(phase: VoicePhase.listening, clearError: true);
    } catch (error) {
      await _capture.stop();
      if (mounted && generation == _generation) {
        state = state.copyWith(
          phase: VoicePhase.error,
          error: readableError(error),
        );
      }
    } finally {
      _starting = false;
    }
  }

  void _sendAudio(Uint8List bytes) {
    if (state.phase != VoicePhase.listening || bytes.isEmpty) return;
    final turnId = state.turnId;
    if (turnId == null) return;
    final sequence = _sequence++;
    unawaited(
      _transport.sendAudio(turnId, sequence, bytes).catchError((Object error) {
        _fail(error);
      }),
    );
  }

  Future<void> stop() async {
    final turnId = state.turnId;
    if (turnId == null) return;
    await _capture.stop();
    await _transport.end(turnId);
    if (mounted) state = state.copyWith(phase: VoicePhase.processing);
  }

  Future<void> cancel() async {
    final turnId = state.turnId;
    ++_generation;
    await _capture.stop();
    if (turnId != null) await _transport.cancel(turnId);
    await _player.stop();
    if (mounted) state = const VoiceState();
  }

  Future<void> interrupt() async {
    final turnId = state.turnId;
    ++_generation;
    await _capture.stop();
    if (turnId != null) await _transport.interrupt(turnId);
    await _player.stop();
    if (mounted) state = const VoiceState();
  }

  void _onEvent(VoiceEvent event) {
    if (!mounted) return;
    switch (event) {
      case VoiceTransportError(:final error):
        _fail(error);
      case VoiceTransportDisconnected():
        _fail(StateError('语音连接已断开'));
      case VoiceAudioChunk():
        _enqueueAudio(event);
      case VoiceControlEvent():
        _onControl(event);
    }
  }

  void _fail(Object error) {
    unawaited(_capture.stop());
    unawaited(_player.stop());
    if (mounted) {
      state = state.copyWith(
        phase: VoicePhase.error,
        error: readableError(error),
      );
    }
  }

  bool _isActive(String turnId, int generation) =>
      mounted && generation == _generation && state.turnId == turnId;

  void _enqueueAudio(VoiceAudioChunk chunk) {
    if (chunk.turnId != state.turnId ||
        chunk.sequence <= _lastPlaybackSequence ||
        chunk.channels != 1) {
      return;
    }
    _lastPlaybackSequence = chunk.sequence;
    final generation = _generation;
    _playbackQueue = _playbackQueue.then((_) => _playChunk(chunk, generation));
  }

  Future<void> _playChunk(VoiceAudioChunk chunk, int generation) async {
    try {
      if (!_isActive(chunk.turnId, generation)) return;
      final sampleRate = chunk.sampleRate ?? state.sampleRate;
      await _player.prepare(sampleRate: sampleRate);
      if (!_isActive(chunk.turnId, generation)) return;
      state = state.copyWith(
        phase: VoicePhase.speaking,
        sampleRate: sampleRate,
      );
      await _player.play(chunk.pcm);
    } catch (error) {
      if (_isActive(chunk.turnId, generation)) _fail(error);
    }
  }

  void _onControl(VoiceControlEvent event) {
    final data = event.data;
    final eventTurnId = data['turn_id']?.toString();
    if (eventTurnId != null &&
        state.turnId != null &&
        eventTurnId != state.turnId) {
      return;
    }
    switch (event.type) {
      case 'started':
        return;
      case 'state':
        final serverState = data['state']?.toString();
        final phase = switch (serverState) {
          'listening' => VoicePhase.listening,
          'transcribing' || 'thinking' => VoicePhase.processing,
          'speaking' => VoicePhase.speaking,
          'idle' => VoicePhase.idle,
          'closed' => VoicePhase.error,
          _ => state.phase,
        };
        state = state.copyWith(
          phase: phase,
          error: serverState == 'closed' ? '语音会话已关闭' : null,
        );
      case 'transcript' || 'stt.final' || 'transcript.user':
        final text = data['text']?.toString() ?? '';
        if (text.trim().isNotEmpty) _onTranscript?.call('user', text);
      case 'text':
        _assistantBuffer += data['delta']?.toString() ?? '';
      case 'done' || 'tts.end' || 'turn.end' || 'turn.completed':
        final text = (data['text']?.toString().isNotEmpty == true)
            ? data['text'].toString()
            : _assistantBuffer;
        if (text.trim().isNotEmpty) _onTranscript?.call('assistant', text);
        _assistantBuffer = '';
        _finishAfterPlayback(eventTurnId ?? state.turnId, _generation);
      case 'interrupted':
        ++_generation;
        unawaited(_player.stop());
        state = const VoiceState();
      case 'error':
        _fail(
          StateError((data['error'] ?? data['message'] ?? '语音服务错误').toString()),
        );
      case 'tts.start':
        state = state.copyWith(
          phase: VoicePhase.speaking,
          sampleRate:
              (data['sample_rate'] as num?)?.toInt() ?? state.sampleRate,
        );
      case 'transcript.assistant' || 'tts.text':
        final text = data['text']?.toString() ?? '';
        if (text.trim().isNotEmpty) _onTranscript?.call('assistant', text);
    }
  }

  void _finishAfterPlayback(String? turnId, int generation) {
    if (turnId == null) return;
    _playbackQueue = _playbackQueue.then((_) {
      if (_isActive(turnId, generation)) {
        state = state.copyWith(phase: VoicePhase.idle);
      }
    });
  }

  @override
  void dispose() {
    ++_generation;
    unawaited(_eventSubscription.cancel());
    unawaited(_capture.dispose());
    unawaited(_player.dispose());
    unawaited(_transport.close());
    super.dispose();
  }
}
