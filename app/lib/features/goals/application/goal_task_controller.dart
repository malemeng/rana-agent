import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/storage/local_store.dart';
import '../../../core/utils/errors.dart';
import '../../../shared/models/models.dart';
import '../data/goal_task_repository.dart';

class GoalTaskState {
  const GoalTaskState({
    this.goals = const <Goal>[],
    this.tasks = const <AgentTask>[],
    this.isLoading = false,
    this.isCreating = false,
    this.error,
    this.busyTaskIds = const <String>{},
  });

  final List<Goal> goals;
  final List<AgentTask> tasks;
  final bool isLoading;
  final bool isCreating;
  final String? error;
  final Set<String> busyTaskIds;

  GoalTaskState copyWith({
    List<Goal>? goals,
    List<AgentTask>? tasks,
    bool? isLoading,
    bool? isCreating,
    String? error,
    bool clearError = false,
    Set<String>? busyTaskIds,
  }) => GoalTaskState(
    goals: goals ?? this.goals,
    tasks: tasks ?? this.tasks,
    isLoading: isLoading ?? this.isLoading,
    isCreating: isCreating ?? this.isCreating,
    error: clearError ? null : error ?? this.error,
    busyTaskIds: busyTaskIds ?? this.busyTaskIds,
  );
}

class GoalTaskController extends StateNotifier<GoalTaskState> {
  GoalTaskController(this._repository, this._localStore)
    : super(GoalTaskState(goals: _localStore.readGoals()));

  final GoalTaskRepository _repository;
  final LocalStore _localStore;

  Future<void> refresh() async {
    state = state.copyWith(isLoading: true, clearError: true);
    try {
      final goals = await _repository.fetchGoals();
      final tasks = await _repository.fetchTasks();
      await _localStore.saveGoals(goals);
      if (!mounted) return;
      state = state.copyWith(goals: goals, tasks: tasks, isLoading: false);
    } catch (error) {
      if (!mounted) return;
      state = state.copyWith(isLoading: false, error: readableError(error));
    }
  }

  Future<Goal> createGoal(String input) async {
    state = state.copyWith(isCreating: true, clearError: true);
    try {
      final goal = await _repository.createGoal(input);
      if (goal.id.isEmpty) {
        throw const FormatException('创建目标响应缺少 goal id');
      }
      final goals = <Goal>[goal, ...state.goals];
      await _localStore.saveGoals(goals);
      if (!mounted) return goal;
      state = state.copyWith(goals: goals, isCreating: false);
      return goal;
    } catch (error) {
      if (mounted) {
        state = state.copyWith(isCreating: false, error: readableError(error));
      }
      rethrow;
    }
  }

  Future<void> cancelTask(String id) => _taskAction(id, true);

  Future<void> retryTask(String id) => _taskAction(id, false);

  Future<void> _taskAction(String id, bool cancel) async {
    state = state.copyWith(
      busyTaskIds: <String>{...state.busyTaskIds, id},
      clearError: true,
    );
    try {
      final updated = cancel
          ? await _repository.cancelTask(id)
          : await _repository.retryTask(id);
      final tasks = state.tasks
          .map((task) {
            if (task.id != id) return task;
            return updated ??
                task.copyWith(status: cancel ? 'cancelled' : 'retrying');
          })
          .toList(growable: false);
      if (mounted) state = state.copyWith(tasks: tasks);
    } catch (error) {
      if (mounted) state = state.copyWith(error: readableError(error));
    } finally {
      if (mounted) {
        final busy = <String>{...state.busyTaskIds}..remove(id);
        state = state.copyWith(busyTaskIds: busy);
      }
    }
  }

  void handleEvent(ServerEvent event) {
    final type = event.type;
    if (type == 'goal.deleted') {
      final id = event.data['goal_id']?.toString() ?? '';
      final goals = state.goals.where((goal) => goal.id != id).toList();
      state = state.copyWith(goals: goals);
      unawaited(_localStore.saveGoals(goals));
    } else if (type == 'task.deleted') {
      final id = event.data['task_id']?.toString() ?? '';
      state = state.copyWith(
        tasks: state.tasks.where((task) => task.id != id).toList(),
      );
    } else if (type.startsWith('goal.') || type.startsWith('task.')) {
      // 稀疏事件只作为失效通知，再读取完整资源，避免覆盖已有字段。
      unawaited(refresh());
    }
  }
}
