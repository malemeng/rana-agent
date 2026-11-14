import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/config/app_config.dart';

void main() {
  test('REST Base URL 正确转换为事件与语音 WSS 地址', () {
    expect(
      AppConfig.eventsUri(
        'https://agent.example.com/api/v1/',
        'a b',
      ).toString(),
      'wss://agent.example.com/ws/events?token=a+b',
    );
    expect(
      AppConfig.eventsUri(
        'https://agent.example.com/api/v1',
        'token',
        after: 42,
      ).toString(),
      'wss://agent.example.com/ws/events?token=token&after=42',
    );
    expect(
      AppConfig.voiceUri('http://127.0.0.1:8000/api/v1', 'token').toString(),
      'ws://127.0.0.1:8000/ws/voice?token=token',
    );
  });

  test('无协议地址默认使用 http 且移除末尾斜线', () {
    expect(
      AppConfig.normalizeBaseUrl('localhost:8000/api/v1/'),
      'http://localhost:8000/api/v1',
    );
  });
}
