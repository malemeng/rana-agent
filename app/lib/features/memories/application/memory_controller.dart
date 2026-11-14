import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/errors.dart';
import '../../../shared/application/list_state.dart';
import '../../../shared/models/models.dart';
import '../data/memory_repository.dart';

class MemoryController extends StateNotifier<ListState<MemoryItem>> {
  MemoryController(this._repository) : super(const ListState<MemoryItem>());

  final MemoryRepository _repository;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final items = await _repository.fetchAll();
      if (mounted) state = state.copyWith(items: items, isLoading: false);
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isLoading: false, error: readableError(error));
      }
    }
  }

  Future<void> save({
    String? id,
    required String content,
    required List<String> tags,
  }) async {
    try {
      final saved = id == null
          ? await _repository.create(content, tags)
          : await _repository.update(id, content, tags);
      final exists = state.items.any((item) => item.id == saved.id);
      final items = exists
          ? state.items
                .map((item) => item.id == saved.id ? saved : item)
                .toList(growable: false)
          : <MemoryItem>[saved, ...state.items];
      if (mounted) state = state.copyWith(items: items, clearError: true);
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
      rethrow;
    }
  }

  Future<void> delete(String id) async {
    state = state.copyWith(busyIds: <String>{...state.busyIds, id});
    try {
      await _repository.delete(id);
      if (mounted) {
        state = state.copyWith(
          items: state.items.where((item) => item.id != id).toList(),
        );
      }
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
    } finally {
      if (mounted) {
        state = state.copyWith(busyIds: <String>{...state.busyIds}..remove(id));
      }
    }
  }

  void handleEvent(ServerEvent event) {
    if (!event.type.startsWith('memory.')) return;
    if (event.type == 'memory.deleted') {
      final id =
          event.data['memory_id']?.toString() ??
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
