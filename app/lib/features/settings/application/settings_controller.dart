import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../config/app_config.dart';
import '../../../core/network/api_client.dart';
import '../../../core/storage/local_store.dart';
import '../../../core/utils/errors.dart';
import '../../../shared/models/models.dart';
import '../data/settings_repository.dart';

class SettingsState {
  const SettingsState({
    required this.baseUrl,
    this.voiceEnabled = true,
    this.notificationsEnabled = true,
    this.themeMode = ThemeMode.system,
    this.remote = const <String, dynamic>{},
    this.isLoading = false,
    this.isTesting = false,
    this.error,
    this.connectionResult,
  });

  final String baseUrl;
  final bool voiceEnabled;
  final bool notificationsEnabled;
  final ThemeMode themeMode;
  final Map<String, dynamic> remote;
  final bool isLoading;
  final bool isTesting;
  final String? error;
  final ConnectionResult? connectionResult;

  SettingsState copyWith({
    String? baseUrl,
    bool? voiceEnabled,
    bool? notificationsEnabled,
    ThemeMode? themeMode,
    Map<String, dynamic>? remote,
    bool? isLoading,
    bool? isTesting,
    String? error,
    bool clearError = false,
    ConnectionResult? connectionResult,
  }) => SettingsState(
    baseUrl: baseUrl ?? this.baseUrl,
    voiceEnabled: voiceEnabled ?? this.voiceEnabled,
    notificationsEnabled: notificationsEnabled ?? this.notificationsEnabled,
    themeMode: themeMode ?? this.themeMode,
    remote: remote ?? this.remote,
    isLoading: isLoading ?? this.isLoading,
    isTesting: isTesting ?? this.isTesting,
    error: clearError ? null : error ?? this.error,
    connectionResult: connectionResult ?? this.connectionResult,
  );
}

class SettingsController extends StateNotifier<SettingsState> {
  SettingsController(this._repository, this._localStore, this._apiClient)
    : super(
        SettingsState(
          baseUrl: _localStore.baseUrl,
          voiceEnabled: _localStore.voiceEnabled,
          notificationsEnabled: _localStore.notificationsEnabled,
          themeMode: switch (_localStore.theme) {
            'light' => ThemeMode.light,
            'dark' => ThemeMode.dark,
            _ => ThemeMode.system,
          },
        ),
      );

  final SettingsRepository _repository;
  final LocalStore _localStore;
  final ApiClient _apiClient;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final remote = await _repository.fetchSettings();
      if (mounted) {
        state = state.copyWith(remote: remote, isLoading: false);
      }
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isLoading: false, error: readableError(error));
      }
    }
  }

  Future<void> saveBaseUrl(String value) async {
    final normalized = AppConfig.normalizeBaseUrl(value);
    await _localStore.setBaseUrl(normalized);
    _apiClient.updateBaseUrl(normalized);
    if (mounted) state = state.copyWith(baseUrl: normalized);
  }

  Future<void> setVoiceEnabled(bool value) async {
    await _localStore.setVoiceEnabled(value);
    if (mounted) state = state.copyWith(voiceEnabled: value);
  }

  Future<void> setNotificationsEnabled(bool value) async {
    await _localStore.setNotificationsEnabled(value);
    if (mounted) state = state.copyWith(notificationsEnabled: value);
  }

  Future<void> setThemeMode(ThemeMode value) async {
    await _localStore.setTheme(value.name);
    if (mounted) state = state.copyWith(themeMode: value);
  }

  Future<void> testConnection() async {
    state = state.copyWith(isTesting: true, clearError: true);
    try {
      final result = await _repository.testConnection();
      if (mounted) {
        state = state.copyWith(isTesting: false, connectionResult: result);
      }
    } catch (error) {
      if (mounted) {
        state = state.copyWith(
          isTesting: false,
          connectionResult: ConnectionResult(
            success: false,
            message: readableError(error),
          ),
        );
      }
    }
  }
}
