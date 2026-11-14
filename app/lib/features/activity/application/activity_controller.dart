import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/errors.dart';
import '../../../core/utils/id.dart';
import '../../../shared/application/list_state.dart';
import '../../../shared/models/models.dart';
import '../data/activity_repository.dart';

class ActivityController extends StateNotifier<ListState<ActivityItem>> {
  ActivityController(this._repository) : super(const ListState<ActivityItem>());

  final ActivityRepository _repository;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final items = await _repository.fetchActivities();
      if (mounted) state = state.copyWith(items: items, isLoading: false);
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isLoading: false, error: readableError(error));
      }
    }
  }

  void handleEvent(ServerEvent event) {
    if (event.type.startsWith('message.') ||
        event.type.startsWith('chat.') ||
        event.type == 'unknown' ||
        event.type == 'ping') {
      return;
    }
    final item = ActivityItem.fromJson(<String, dynamic>{
      'id': event.data['event_id'] ?? createId('event'),
      'type': event.type,
      'title': event.data['title'] ?? event.data['message'] ?? event.type,
      'description': event.data['description'] ?? event.data['detail'] ?? '',
      'created_at':
          event.data['created_at'] ?? DateTime.now().toIso8601String(),
    });
    if (state.items.any((existing) => existing.id == item.id)) return;
    state = state.copyWith(items: <ActivityItem>[item, ...state.items]);
  }
}
