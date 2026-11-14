import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/app_config.dart';
import '../core/network/event_transport.dart';
import '../core/storage/token_storage.dart';
import '../core/utils/errors.dart';
import '../shared/models/models.dart';

class RealtimeState {
  const RealtimeState({this.connected = false, this.error});

  final bool connected;
  final String? error;
}

class RealtimeHandlers {
  const RealtimeHandlers({
    required this.chat,
    required this.goalTask,
    required this.approval,
    required this.activity,
    required this.memory,
    required this.schedule,
  });

  final void Function(ServerEvent event) chat;
  final void Function(ServerEvent event) goalTask;
  final void Function(ServerEvent event) approval;
  final void Function(ServerEvent event) activity;
  final void Function(ServerEvent event) memory;
  final void Function(ServerEvent event) schedule;
}

class RealtimeController extends StateNotifier<RealtimeState> {
  RealtimeController({
    required EventsTransport Function() transportFactory,
    required TokenStorage tokenStorage,
    required String Function() baseUrl,
    required RealtimeHandlers handlers,
    required Future<bool> Function() refreshSession,
    required void Function() onSessionExpired,
  }) : _transportFactory = transportFactory,
       _tokenStorage = tokenStorage,
       _baseUrl = baseUrl,
       _handlers = handlers,
       _refreshSession = refreshSession,
       _onSessionExpired = onSessionExpired,
       super(const RealtimeState());

  final EventsTransport Function() _transportFactory;
  final TokenStorage _tokenStorage;
  final String Function() _baseUrl;
  final RealtimeHandlers _handlers;
  final Future<bool> Function() _refreshSession;
  final void Function() _onSessionExpired;
  EventsTransport? _transport;
  StreamSubscription<ServerEvent>? _subscription;
  Timer? _reconnectTimer;
  bool _active = false;
  int _retrySeconds = 2;
  int _lastActivityId = 0;
  final Set<int> _seenActivityIds = <int>{};

  Future<void> connect() async {
    await stop();
    _active = true;
    final token = await _tokenStorage.readAccessToken();
    if (!_active || !mounted) return;
    if (token == null || token.isEmpty) return;
    try {
      final transport = _transportFactory();
      _transport = transport;
      final stream = await transport.connect(
        AppConfig.eventsUri(_baseUrl(), token, after: _lastActivityId),
      );
      if (!_active || !mounted) return;
      state = const RealtimeState(connected: true);
      _retrySeconds = 2;
      _subscription = stream.listen(
        _dispatch,
        onError: (Object error) {
          if (mounted) {
            state = RealtimeState(
              connected: false,
              error: readableError(error),
            );
          }
          _scheduleReconnect();
        },
        onDone: () => unawaited(_handleDone(transport)),
      );
    } catch (error) {
      if (mounted) {
        state = RealtimeState(connected: false, error: readableError(error));
      }
      _scheduleReconnect();
    }
  }

  void _dispatch(ServerEvent event) {
    final activityId = event.activityId;
    if (activityId != null && _seenActivityIds.contains(activityId)) return;
    _handlers.chat(event);
    _handlers.goalTask(event);
    _handlers.approval(event);
    _handlers.activity(event);
    _handlers.memory(event);
    _handlers.schedule(event);
    if (activityId != null) {
      _seenActivityIds.add(activityId);
      if (_seenActivityIds.length > 512) {
        _seenActivityIds.remove(_seenActivityIds.first);
      }
      if (activityId > _lastActivityId) _lastActivityId = activityId;
    }
  }

  Future<void> _handleDone(EventsTransport transport) async {
    if (!_active || !mounted) return;
    state = const RealtimeState();
    if (transport.closeCode == 4401) {
      try {
        final refreshed = await _refreshSession();
        if (!refreshed) {
          _onSessionExpired();
          return;
        }
      } catch (error) {
        if (mounted) {
          state = RealtimeState(error: readableError(error));
        }
        _scheduleReconnect();
        return;
      }
    }
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (!_active || !mounted || _reconnectTimer?.isActive == true) return;
    if (state.connected) state = const RealtimeState();
    _reconnectTimer = Timer(Duration(seconds: _retrySeconds), () {
      _retrySeconds = (_retrySeconds * 2).clamp(2, 30).toInt();
      unawaited(connect());
    });
  }

  Future<void> stop() async {
    _active = false;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _subscription?.cancel();
    _subscription = null;
    await _transport?.close();
    _transport = null;
    if (mounted && state.connected) state = const RealtimeState();
  }

  Future<void> reset() async {
    await stop();
    _lastActivityId = 0;
    _seenActivityIds.clear();
    if (mounted) state = const RealtimeState();
  }

  @override
  void dispose() {
    unawaited(stop());
    super.dispose();
  }
}
