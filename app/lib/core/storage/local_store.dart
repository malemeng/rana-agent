import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../../config/app_config.dart';
import '../../shared/models/models.dart';

class LocalStore {
  const LocalStore(this._preferences);

  static const _baseUrlKey = 'server.base_url';
  static const _usernameKey = 'auth.username';
  static const _goalsPrefix = 'cache.goals';
  static const _voiceKey = 'preferences.voice';
  static const _notificationsKey = 'preferences.notifications';
  static const _themeKey = 'preferences.theme';

  final SharedPreferences _preferences;

  String get _goalsKey =>
      '$_goalsPrefix.${_preferences.getString(_usernameKey) ?? 'anonymous'}';

  String get baseUrl =>
      _preferences.getString(_baseUrlKey) ?? AppConfig.defaultBaseUrl;
  String get username => _preferences.getString(_usernameKey) ?? '';
  bool get voiceEnabled => _preferences.getBool(_voiceKey) ?? true;
  bool get notificationsEnabled =>
      _preferences.getBool(_notificationsKey) ?? true;
  String get theme => _preferences.getString(_themeKey) ?? 'system';

  Future<void> setBaseUrl(String value) =>
      _preferences.setString(_baseUrlKey, AppConfig.normalizeBaseUrl(value));
  Future<void> setUsername(String value) =>
      _preferences.setString(_usernameKey, value);
  Future<void> setVoiceEnabled(bool value) =>
      _preferences.setBool(_voiceKey, value);
  Future<void> setNotificationsEnabled(bool value) =>
      _preferences.setBool(_notificationsKey, value);
  Future<void> setTheme(String value) =>
      _preferences.setString(_themeKey, value);

  List<Goal> readGoals() {
    final raw = _preferences.getString(_goalsKey);
    if (raw == null || raw.isEmpty) return const <Goal>[];
    try {
      return (jsonDecode(raw) as List<dynamic>)
          .map((dynamic value) => Goal.fromJson(asJsonMap(value)))
          .toList(growable: false);
    } on FormatException {
      return const <Goal>[];
    }
  }

  Future<void> saveGoals(List<Goal> goals) => _preferences.setString(
    _goalsKey,
    jsonEncode(goals.map((goal) => goal.toJson()).toList(growable: false)),
  );
}
