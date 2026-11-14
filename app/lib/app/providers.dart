import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../core/network/api_client.dart';
import '../core/network/event_transport.dart';
import '../core/storage/local_store.dart';
import '../core/storage/token_storage.dart';
import '../features/activity/application/activity_controller.dart';
import '../features/activity/data/activity_repository.dart';
import '../features/approvals/application/approval_controller.dart';
import '../features/approvals/data/approval_repository.dart';
import '../features/auth/application/auth_controller.dart';
import '../features/auth/data/auth_repository.dart';
import '../features/chat/application/chat_controller.dart';
import '../features/chat/data/conversation_repository.dart';
import '../features/goals/application/goal_task_controller.dart';
import '../features/goals/data/goal_task_repository.dart';
import '../features/memories/application/memory_controller.dart';
import '../features/memories/data/memory_repository.dart';
import '../features/schedule/application/schedule_controller.dart';
import '../features/schedule/data/schedule_repository.dart';
import '../features/settings/application/settings_controller.dart';
import '../features/settings/data/settings_repository.dart';
import '../features/voice/application/voice_controller.dart';
import '../features/voice/data/audio_io.dart';
import '../features/voice/data/voice_transport.dart';
import '../shared/application/list_state.dart';
import '../shared/models/models.dart';
import 'realtime_controller.dart';

final sharedPreferencesProvider = Provider<SharedPreferences>(
  (ref) => throw UnimplementedError('请在 main() 中覆盖 sharedPreferencesProvider'),
);

final localStoreProvider = Provider<LocalStore>(
  (ref) => LocalStore(ref.watch(sharedPreferencesProvider)),
);

final tokenStorageProvider = Provider<TokenStorage>(
  (ref) => const SecureTokenStorage(FlutterSecureStorage()),
);

final apiClientProvider = Provider<ApiClient>((ref) {
  final client = ApiClient(
    baseUrl: ref.watch(localStoreProvider).baseUrl,
    tokenStorage: ref.watch(tokenStorageProvider),
  );
  ref.onDispose(client.close);
  return client;
});

final authGatewayProvider = Provider<AuthGateway>((ref) {
  final repository = AuthRepository(
    ref.watch(apiClientProvider),
    ref.watch(tokenStorageProvider),
  );
  ref.watch(apiClientProvider).refreshHandler = repository.refresh;
  return repository;
});

final sessionGenerationProvider = StateProvider<int>((ref) => 0);

final authControllerProvider = StateNotifierProvider<AuthController, AuthState>(
  (ref) {
    final controller = AuthController(
      gateway: ref.watch(authGatewayProvider),
      tokenStorage: ref.watch(tokenStorageProvider),
      localStore: ref.watch(localStoreProvider),
      apiClient: ref.watch(apiClientProvider),
      onSessionChanged: () =>
          ref.read(sessionGenerationProvider.notifier).state++,
    );
    ref.watch(apiClientProvider).sessionExpiredHandler = () =>
        unawaited(controller.expire());
    return controller;
  },
);

final goalTaskRepositoryProvider = Provider<GoalTaskRepository>(
  (ref) => GoalTaskRepository(ref.watch(apiClientProvider)),
);

final goalTaskControllerProvider =
    StateNotifierProvider<GoalTaskController, GoalTaskState>((ref) {
      ref.watch(sessionGenerationProvider);
      return GoalTaskController(
        ref.watch(goalTaskRepositoryProvider),
        ref.watch(localStoreProvider),
      );
    });

final conversationRepositoryProvider = Provider<ConversationRepository>((ref) {
  ref.watch(sessionGenerationProvider);
  return ConversationRepository(ref.watch(apiClientProvider));
});

final chatControllerProvider = StateNotifierProvider<ChatController, ChatState>(
  (ref) {
    ref.watch(sessionGenerationProvider);
    return ChatController(
      repository: ref.watch(conversationRepositoryProvider),
    );
  },
);

final approvalRepositoryProvider = Provider<ApprovalRepository>(
  (ref) => ApprovalRepository(ref.watch(apiClientProvider)),
);

final approvalControllerProvider =
    StateNotifierProvider<ApprovalController, ListState<ApprovalItem>>((ref) {
      ref.watch(sessionGenerationProvider);
      return ApprovalController(
        ref.watch(approvalRepositoryProvider),
        onDecision: () =>
            ref.read(goalTaskControllerProvider.notifier).refresh(),
      );
    });

final activityRepositoryProvider = Provider<ActivityRepository>(
  (ref) => ActivityRepository(ref.watch(apiClientProvider)),
);

final activityControllerProvider =
    StateNotifierProvider<ActivityController, ListState<ActivityItem>>((ref) {
      ref.watch(sessionGenerationProvider);
      return ActivityController(ref.watch(activityRepositoryProvider));
    });

final memoryRepositoryProvider = Provider<MemoryRepository>(
  (ref) => MemoryRepository(ref.watch(apiClientProvider)),
);

final memoryControllerProvider =
    StateNotifierProvider<MemoryController, ListState<MemoryItem>>((ref) {
      ref.watch(sessionGenerationProvider);
      return MemoryController(ref.watch(memoryRepositoryProvider));
    });

final scheduleRepositoryProvider = Provider<ScheduleRepository>(
  (ref) => ScheduleRepository(ref.watch(apiClientProvider)),
);

final scheduleControllerProvider =
    StateNotifierProvider<ScheduleController, ListState<ScheduleItem>>((ref) {
      ref.watch(sessionGenerationProvider);
      return ScheduleController(ref.watch(scheduleRepositoryProvider));
    });

final settingsRepositoryProvider = Provider<SettingsRepository>(
  (ref) => SettingsRepository(ref.watch(apiClientProvider)),
);

final settingsControllerProvider =
    StateNotifierProvider<SettingsController, SettingsState>((ref) {
      ref.watch(sessionGenerationProvider);
      return SettingsController(
        ref.watch(settingsRepositoryProvider),
        ref.watch(localStoreProvider),
        ref.watch(apiClientProvider),
      );
    });

final voiceTransportProvider = Provider<VoiceTransport>(
  (ref) => WebSocketVoiceTransport(),
);
final audioCaptureProvider = Provider<AudioCapture>(
  (ref) => RecordAudioCapture(),
);
final audioPlayerProvider = Provider<AudioPlayer>(
  (ref) => FlutterPcmSoundAudioPlayer(),
);
final microphonePermissionProvider = Provider<MicrophonePermissionGateway>(
  (ref) => const PermissionHandlerMicrophoneGateway(),
);

final voiceControllerProvider =
    StateNotifierProvider<VoiceController, VoiceState>((ref) {
      ref.watch(sessionGenerationProvider);
      return VoiceController(
        transport: ref.watch(voiceTransportProvider),
        capture: ref.watch(audioCaptureProvider),
        player: ref.watch(audioPlayerProvider),
        permission: ref.watch(microphonePermissionProvider),
        tokenStorage: ref.watch(tokenStorageProvider),
        baseUrl: () => ref.read(apiClientProvider).baseUrl,
        onTranscript: (role, text) =>
            ref.read(chatControllerProvider.notifier).addTranscript(role, text),
      );
    });

final realtimeControllerProvider =
    StateNotifierProvider<RealtimeController, RealtimeState>((ref) {
      final controller = RealtimeController(
        transportFactory: WebSocketEventsTransport.new,
        tokenStorage: ref.watch(tokenStorageProvider),
        baseUrl: () => ref.read(apiClientProvider).baseUrl,
        refreshSession: ref.watch(apiClientProvider).refreshSession,
        onSessionExpired: () =>
            unawaited(ref.read(authControllerProvider.notifier).expire()),
        handlers: RealtimeHandlers(
          chat: (event) =>
              ref.read(chatControllerProvider.notifier).handleEvent(event),
          goalTask: (event) =>
              ref.read(goalTaskControllerProvider.notifier).handleEvent(event),
          approval: (event) =>
              ref.read(approvalControllerProvider.notifier).handleEvent(event),
          activity: (event) =>
              ref.read(activityControllerProvider.notifier).handleEvent(event),
          memory: (event) =>
              ref.read(memoryControllerProvider.notifier).handleEvent(event),
          schedule: (event) =>
              ref.read(scheduleControllerProvider.notifier).handleEvent(event),
        ),
      );
      ref.listen<AuthState>(authControllerProvider, (previous, next) {
        if (next.status == AuthStatus.authenticated) {
          unawaited(controller.connect());
        } else {
          unawaited(controller.reset());
        }
      }, fireImmediately: true);
      return controller;
    });
