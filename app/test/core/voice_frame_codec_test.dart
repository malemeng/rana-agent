import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/features/voice/data/voice_transport.dart';

void main() {
  group('VoiceFrameCodec', () {
    test('按大端 JSON 头长度编解码 PCM 帧', () {
      final pcm = Uint8List.fromList(<int>[1, 0, 255, 127, 0, 128]);
      final encoded = VoiceFrameCodec.encode(
        turnId: 'turn-1',
        sequence: 7,
        pcm: pcm,
      );

      expect(
        ByteData.sublistView(encoded).getUint32(0, Endian.big),
        greaterThan(0),
      );
      final decoded = VoiceFrameCodec.decode(encoded);
      expect(decoded.header['kind'], 'audio');
      expect(decoded.header['turn_id'], 'turn-1');
      expect(decoded.header['seq'], 7);
      expect(decoded.pcm, pcm);
    });

    test('拒绝截断的帧', () {
      expect(
        () => VoiceFrameCodec.decode(<int>[0, 0, 0, 20, 123, 125]),
        throwsFormatException,
      );
    });
  });
}
