import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/core/network/api_client.dart';
import 'package:personal_agent/core/storage/local_store.dart';
import 'package:personal_agent/core/storage/token_storage.dart';
import 'package:personal_agent/features/auth/application/auth_controller.dart';
import 'package:personal_agent/features/auth/data/auth_repository.dart';
import 'package:personal_agent/shared/models/models.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('登录保存安全令牌、Base URL 与用户名', () async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    final preferences = await SharedPreferences.getInstance();
    final storage = InMemoryTokenStorage();
    final gateway = _FakeAuthGateway();
    final client = ApiClient(
      baseUrl: 'http://localhost:8000/api/v1',
      tokenStorage: storage,
    );
    final controller = AuthController(
      gateway: gateway,
      tokenStorage: storage,
      localStore: LocalStore(preferences),
      apiClient: client,
    );
    addTearDown(controller.dispose);
    addTearDown(client.close);

    await controller.login(
      baseUrl: 'https://agent.example.com/api/v1/',
      username: 'tester',
      password: 'secret',
    );

    expect(controller.state.status, AuthStatus.authenticated);
    expect(storage.tokens?.accessToken, 'access');
    expect(client.baseUrl, 'https://agent.example.com/api/v1');
    expect(preferences.getString('auth.username'), 'tester');
    expect(gateway.lastPassword, 'secret');
  });
}

class _FakeAuthGateway implements AuthGateway {
  String? lastPassword;

  @override
  Future<AuthTokens> login(String username, String password) async {
    lastPassword = password;
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
