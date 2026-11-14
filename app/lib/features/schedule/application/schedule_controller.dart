import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/errors.dart';
import '../../../shared/application/list_state.dart';
import '../../../shared/models/models.dart';
import '../data/schedule_repository.dart';

class ScheduleController extends StateNotifier<ListState<ScheduleItem>> {
  ScheduleController(this._repository) : super(const ListState<ScheduleItem>());

  final ScheduleRepository _repository;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final items = await _repository.fetchAll();
      items.sort((a, b) {
        final aTime = a.nextRunAt;
        final bTime = b.nextRunAt;
        if (aTime == null && bTime == null) return 0;
        if (aTime == null) return 1;
        if (bTime == null) return -1;
        return aTime.compareTo(bTime);
      });
      if (mounted) state = state.copyWith(items: items, isLoading: false);
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isLoading: false, error: readableError(error));
      }
    }
  }

  Future<void> create(ScheduleItem schedule) async {
    try {
      final created = await _repository.create(schedule);
      if (mounted) {
        state = state.copyWith(
          items: <ScheduleItem>[...state.items, created],
          clearError: true,
        );
      }
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
      rethrow;
    }
  }

  Future<void> setEnabled(ScheduleItem schedule, bool value) async {
    try {
      final updated = await _repository.setEnabled(schedule, value);
      if (!mounted) return;
      state = state.copyWith(
        items: state.items.map((item) {
          if (item.id != schedule.id) return item;
          return updated ?? item.copyWith(enabled: value);
        }).toList(),
      );
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
    }
  }

  Future<void> delete(String id) async {
    try {
      await _repository.delete(id);
      if (mounted) {
        state = state.copyWith(
          items: state.items.where((item) => item.id != id).toList(),
        );
      }
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
    }
  }

  void handleEvent(ServerEvent event) {
    if (!event.type.startsWith('schedule.')) return;
    if (event.type == 'schedule.deleted') {
      final id =
          event.data['schedule_id']?.toString() ??
          event.data['id']?.toString() ??
          '';
      state = state.copyWith(
        items: state.items.where((item) => item.id != id).toList(),
      );
    } else {
      unawaited(refresh());
    }
  }
}
