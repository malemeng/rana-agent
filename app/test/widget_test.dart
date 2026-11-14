import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/app.dart';
import 'package:personal_agent/app/providers.dart';
import 'package:personal_agent/core/storage/token_storage.dart';
import 'package:personal_agent/features/auth/data/auth_repository.dart';
import 'package:personal_agent/features/voice/data/fake_audio.dart';
import 'package:personal_agent/features/voice/presentation/voice_button.dart';
import 'package:personal_agent/shared/models/models.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('未登录显示服务器配对表单', (tester) async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final preferences = await SharedPreferences.getInstance();
    await tester.pumpWidget(
      ProviderScope(
        overrides: <Override>[
          sharedPreferencesProvider.overrideWithValue(preferences),
          tokenStorageProvider.overrideWithValue(InMemoryTokenStorage()),
          authGatewayProvider.overrideWithValue(_FakeAuthGateway()),
        ],
        child: const RanaAgentApp(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('服务器配对 / 登录'), findsOneWidget);
    expect(find.byKey(const Key('baseUrlField')), findsOneWidget);
    expect(find.byKey(const Key('loginButton')), findsOneWidget);
  });

  testWidgets('语音按钮使用内存采集与传输假实现', (tester) async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final preferences = await SharedPreferences.getInstance();
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
    await tester.pumpWidget(
      ProviderScope(
        overrides: <Override>[
          sharedPreferencesProvider.overrideWithValue(preferences),
          tokenStorageProvider.overrideWithValue(tokens),
          voiceTransportProvider.overrideWithValue(transport),
          audioCaptureProvider.overrideWithValue(capture),
          audioPlayerProvider.overrideWithValue(player),
          microphonePermissionProvider.overrideWithValue(
            FakeMicrophonePermission(),
          ),
        ],
        child: const MaterialApp(home: Scaffold(body: VoiceButton())),
      ),
    );

    await tester.tap(find.byKey(const Key('voiceButton')));
    await tester.pump();
    await tester.pump();
    expect(capture.isRecording, isTrue);
    expect(transport.controls, contains('start'));

    await tester.tap(find.byKey(const Key('voiceButton')));
    await tester.pump();
    expect(capture.isRecording, isFalse);
    expect(transport.controls, contains('end'));
  });
}

class _FakeAuthGateway implements AuthGateway {
  @override
  Future<AuthTokens> login(String username, String password) async {
    return const AuthTokens(
      accessToken: 'access',
      refreshToken: 'refresh',
      expiresIn: 3600,
    );
  }

  @override
  Future<bool> refresh() async => true;

  @override
  Future<void> logout() async {}
}
