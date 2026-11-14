import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../config/app_config.dart';
import '../../../core/network/api_client.dart';
import '../../../core/storage/local_store.dart';
import '../../../core/storage/token_storage.dart';
import '../../../core/utils/errors.dart';
import '../data/auth_repository.dart';

enum AuthStatus { restoring, unauthenticated, authenticating, authenticated }

class AuthState {
  const AuthState({required this.status, this.username = '', this.error});

  final AuthStatus status;
  final String username;
  final String? error;
}

class AuthController extends StateNotifier<AuthState> {
  AuthController({
    required AuthGateway gateway,
    required TokenStorage tokenStorage,
    required LocalStore localStore,
    required ApiClient apiClient,
    void Function()? onSessionChanged,
  }) : _gateway = gateway,
       _tokenStorage = tokenStorage,
       _localStore = localStore,
       _apiClient = apiClient,
       _onSessionChanged = onSessionChanged,
       super(const AuthState(status: AuthStatus.restoring)) {
    Future.microtask(restore);
  }

  final AuthGateway _gateway;
  final TokenStorage _tokenStorage;
  final LocalStore _localStore;
  final ApiClient _apiClient;
  final void Function()? _onSessionChanged;

  Future<void> restore() async {
    final token = await _tokenStorage.readAccessToken();
    if (!mounted) return;
    state = AuthState(
      status: token == null || token.isEmpty
          ? AuthStatus.unauthenticated
          : AuthStatus.authenticated,
      username: _localStore.username,
    );
  }

  Future<void> login({
    required String baseUrl,
    required String username,
    required String password,
  }) async {
    state = AuthState(status: AuthStatus.authenticating, username: username);
    try {
      final normalized = AppConfig.normalizeBaseUrl(baseUrl);
      _apiClient.updateBaseUrl(normalized);
      final tokens = await _gateway.login(username, password);
      await _tokenStorage.save(tokens);
      await Future.wait(<Future<void>>[
        _localStore.setBaseUrl(normalized),
        _localStore.setUsername(username),
      ]);
      if (!mounted) return;
      state = AuthState(status: AuthStatus.authenticated, username: username);
      _onSessionChanged?.call();
    } catch (error) {
      if (!mounted) return;
      state = AuthState(
        status: AuthStatus.unauthenticated,
        username: username,
        error: readableError(error),
      );
    }
  }

  Future<void> logout() async {
    try {
      await _gateway.logout();
    } finally {
      await _clearLocalSession();
    }
  }

  Future<void> expire() => _clearLocalSession();

  Future<void> _clearLocalSession() async {
    await _tokenStorage.clear();
    if (!mounted) return;
    state = const AuthState(status: AuthStatus.unauthenticated);
    _onSessionChanged?.call();
  }
}
