import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/core/storage/token_storage.dart';
import 'package:personal_agent/features/voice/application/voice_controller.dart';
import 'package:personal_agent/features/voice/data/fake_audio.dart';
import 'package:personal_agent/features/voice/data/voice_transport.dart';
import 'package:personal_agent/shared/models/models.dart';

void main() {
  test('录音帧发送、结束控制与 TTS PCM 播放', () async {
    final tokens = InMemoryTokenStorage();
    await tokens.save(
      const AuthTokens(
        accessToken: 'access',
        refreshToken: 'refresh',
        expiresIn: 1,
      ),
    );
    final transport = InMemoryVoiceTransport();
    final capture = InMemoryAudioCapture();
    final player = InMemoryAudioPlayer();
    final controller = VoiceController(
      transport: transport,
      capture: capture,
      player: player,
      permission: FakeMicrophonePermission(),
      tokenStorage: tokens,
      baseUrl: () => 'https://agent.example.com/api/v1',
    );
    addTearDown(controller.dispose);

    await controller.start();
    expect(controller.state.phase, VoicePhase.listening);
    expect(capture.isRecording, isTrue);
    expect(transport.uri?.scheme, 'wss');
    expect(transport.uri?.path, '/ws/voice');
    expect(transport.turnId, controller.state.turnId);
    final turnId = controller.state.turnId!;

    capture.emit(Uint8List.fromList(<int>[1, 2, 3, 4]));
    await Future<void>.delayed(Duration.zero);
    expect(transport.frames, hasLength(1));
    final frame = VoiceFrameCodec.decode(transport.frames.single);
    expect(frame.header['seq'], 0);
    expect(frame.pcm, <int>[1, 2, 3, 4]);

    await controller.stop();
    expect(transport.controls, contains('end'));
    expect(controller.state.phase, VoicePhase.processing);

    transport.emit(
      VoiceControlEvent('state', <String, dynamic>{
        'state': 'speaking',
        'turn_id': turnId,
      }),
    );
    await Future<void>.delayed(Duration.zero);
    transport.emit(
      VoiceAudioChunk(
        turnId: turnId,
        sequence: 0,
        sampleRate: 24000,
        pcm: Uint8List.fromList(<int>[0, 0, 1, 0]),
      ),
    );
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
    expect(player.sampleRate, 24000);
    expect(player.chunks, hasLength(1));
    expect(controller.state.phase, VoicePhase.speaking);

    await controller.interrupt();
    transport.emit(
      VoiceAudioChunk(
        turnId: turnId,
        sequence: 1,
        sampleRate: 24000,
        pcm: Uint8List.fromList(<int>[2, 0]),
      ),
    );
    await Future<void>.delayed(Duration.zero);
    expect(player.chunks, hasLength(1));
  });
}
