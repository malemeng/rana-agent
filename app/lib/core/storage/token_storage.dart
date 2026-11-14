import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../../shared/models/models.dart';

abstract class TokenStorage {
  Future<String?> readAccessToken();
  Future<String?> readRefreshToken();
  Future<void> save(AuthTokens tokens);
  Future<void> clear();
}

class SecureTokenStorage implements TokenStorage {
  const SecureTokenStorage(this._storage);

  static const _sessionKey = 'auth.session';
  static const _legacyAccessKey = 'auth.access_token';
  static const _legacyRefreshKey = 'auth.refresh_token';
  static const _legacyExpiresKey = 'auth.expires_in';

  final FlutterSecureStorage _storage;

  Future<Map<String, dynamic>?> _readSession() async {
    final raw = await _storage.read(key: _sessionKey);
    if (raw == null || raw.isEmpty) return null;
    try {
      final value = jsonDecode(raw);
      return value is Map ? asJsonMap(value) : null;
    } on FormatException {
      return null;
    }
  }

  @override
  Future<String?> readAccessToken() async =>
      (await _readSession())?['access_token']?.toString();

  @override
  Future<String?> readRefreshToken() async =>
      (await _readSession())?['refresh_token']?.toString();

  @override
  Future<void> save(AuthTokens tokens) => _storage.write(
    key: _sessionKey,
    value: jsonEncode(<String, dynamic>{
      'access_token': tokens.accessToken,
      'refresh_token': tokens.refreshToken,
      'expires_in': tokens.expiresIn,
    }),
  );

  @override
  Future<void> clear() => Future.wait(<Future<void>>[
    _storage.delete(key: _sessionKey),
    _storage.delete(key: _legacyAccessKey),
    _storage.delete(key: _legacyRefreshKey),
    _storage.delete(key: _legacyExpiresKey),
  ]);
}

class InMemoryTokenStorage implements TokenStorage {
  AuthTokens? tokens;

  @override
  Future<String?> readAccessToken() async => tokens?.accessToken;

  @override
  Future<String?> readRefreshToken() async => tokens?.refreshToken;

  @override
  Future<void> save(AuthTokens tokens) async {
    this.tokens = tokens;
  }

  @override
  Future<void> clear() async {
    tokens = null;
  }
}
