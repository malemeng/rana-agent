import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/errors.dart';
import '../../../shared/application/list_state.dart';
import '../../../shared/models/models.dart';
import '../data/approval_repository.dart';

class ApprovalController extends StateNotifier<ListState<ApprovalItem>> {
  ApprovalController(this._repository, {this.onDecision})
    : super(const ListState<ApprovalItem>());

  final ApprovalRepository _repository;
  final Future<void> Function()? onDecision;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final items = await _repository.fetchApprovals();
      if (mounted) state = state.copyWith(items: items, isLoading: false);
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isLoading: false, error: readableError(error));
      }
    }
  }

  Future<void> approve(String id) => _act(id, true);

  Future<void> reject(String id) => _act(id, false);

  Future<void> _act(String id, bool approve) async {
    state = state.copyWith(
      busyIds: <String>{...state.busyIds, id},
      clearError: true,
    );
    try {
      final updated = approve
          ? await _repository.approve(id)
          : await _repository.reject(id);
      final items = state.items
          .map((item) {
            if (item.id != id) return item;
            return updated ??
                item.copyWith(status: approve ? 'approved' : 'rejected');
          })
          .toList(growable: false);
      if (mounted) state = state.copyWith(items: items);
      await onDecision?.call();
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
    } finally {
      if (mounted) {
        state = state.copyWith(busyIds: <String>{...state.busyIds}..remove(id));
      }
    }
  }

  void handleEvent(ServerEvent event) {
    if (!event.type.startsWith('approval.')) return;
    unawaited(refresh());
  }
}
