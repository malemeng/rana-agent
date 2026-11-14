import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/errors.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/widgets/common.dart';
import '../application/goal_task_controller.dart';

class GoalsTasksPage extends ConsumerStatefulWidget {
  const GoalsTasksPage({super.key});

  @override
  ConsumerState<GoalsTasksPage> createState() => _GoalsTasksPageState();
}

class _GoalsTasksPageState extends ConsumerState<GoalsTasksPage> {
  @override
  void initState() {
    super.initState();
    Future.microtask(
      () => ref.read(goalTaskControllerProvider.notifier).refresh(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(goalTaskControllerProvider);
    return DefaultTabController(
      length: 2,
      child: AgentPageScaffold(
        title: 'Goal / Task',
        actions: <Widget>[
          IconButton(
            onPressed: state.isLoading
                ? null
                : () => ref.read(goalTaskControllerProvider.notifier).refresh(),
            icon: const Icon(Icons.refresh),
            tooltip: '刷新任务',
          ),
        ],
        floatingActionButton: FloatingActionButton.extended(
          onPressed: state.isCreating ? null : () => _showCreateGoal(context),
          icon: const Icon(Icons.add),
          label: const Text('新建目标'),
        ),
        body: Column(
          children: <Widget>[
            const Material(
              child: TabBar(
                tabs: <Widget>[
                  Tab(text: '目标'),
                  Tab(text: '任务'),
                ],
              ),
            ),
            if (state.isLoading) const LinearProgressIndicator(minHeight: 2),
            if (state.error != null)
              Padding(
                padding: const EdgeInsets.all(12),
                child: ErrorBanner(
                  message: state.error!,
                  onRetry: () =>
                      ref.read(goalTaskControllerProvider.notifier).refresh(),
                ),
              ),
            Expanded(
              child: TabBarView(
                children: <Widget>[
                  _GoalsView(state: state),
                  _TasksView(state: state),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _showCreateGoal(BuildContext context) async {
    final controller = TextEditingController();
    final input = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('新建目标'),
        content: TextField(
          controller: controller,
          autofocus: true,
          minLines: 3,
          maxLines: 6,
          decoration: const InputDecoration(hintText: '描述要 Agent 完成的目标'),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('创建'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (input == null || input.trim().isEmpty || !mounted) return;
    try {
      await ref.read(goalTaskControllerProvider.notifier).createGoal(input);
    } catch (error) {
      if (context.mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(readableError(error))));
      }
    }
  }
}

class _GoalsView extends StatelessWidget {
  const _GoalsView({required this.state});

  final GoalTaskState state;

  @override
  Widget build(BuildContext context) {
    if (state.goals.isEmpty && !state.isLoading) {
      return const EmptyState(
        icon: Icons.flag_outlined,
        title: '还没有目标',
        description: '从主对话或右下角新建一个目标。',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.all(16),
      itemCount: state.goals.length,
      itemBuilder: (context, index) {
        final goal = state.goals[index];
        return Card(
          child: ListTile(
            leading: const Icon(Icons.flag_outlined),
            title: Text(goal.title),
            subtitle: Text('${goal.input}\n${formatDateTime(goal.createdAt)}'),
            isThreeLine: true,
            trailing: Chip(label: Text(statusLabel(goal.status))),
          ),
        );
      },
    );
  }
}

class _TasksView extends ConsumerWidget {
  const _TasksView({required this.state});

  final GoalTaskState state;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (state.tasks.isEmpty && !state.isLoading) {
      return const EmptyState(
        icon: Icons.task_alt_outlined,
        title: '暂无任务',
        description: '目标创建后，任务会显示在这里。',
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 88),
      itemCount: state.tasks.length,
      itemBuilder: (context, index) {
        final task = state.tasks[index];
        final busy = state.busyTaskIds.contains(task.id);
        final active = <String>{
          'pending',
          'queued',
          'running',
          'active',
        }.contains(task.status.toLowerCase());
        final canRetry = <String>{
          'failed',
          'cancelled',
          'canceled',
        }.contains(task.status.toLowerCase());
        return Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        task.title,
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                    Chip(label: Text(statusLabel(task.status))),
                  ],
                ),
                Text(
                  'Goal: ${task.goalId} · ${formatDateTime(task.updatedAt ?? task.createdAt)}',
                ),
                if (task.error != null) Text(task.error!),
                if (busy) const LinearProgressIndicator(minHeight: 2),
                OverflowBar(
                  children: <Widget>[
                    if (active)
                      OutlinedButton.icon(
                        onPressed: busy
                            ? null
                            : () => ref
                                  .read(goalTaskControllerProvider.notifier)
                                  .cancelTask(task.id),
                        icon: const Icon(Icons.cancel_outlined),
                        label: const Text('取消任务'),
                      ),
                    if (canRetry)
                      FilledButton.tonalIcon(
                        onPressed: busy
                            ? null
                            : () => ref
                                  .read(goalTaskControllerProvider.notifier)
                                  .retryTask(task.id),
                        icon: const Icon(Icons.replay),
                        label: const Text('重试任务'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
