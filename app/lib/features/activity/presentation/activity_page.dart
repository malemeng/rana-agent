import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/widgets/common.dart';

class ActivityPage extends ConsumerStatefulWidget {
  const ActivityPage({super.key});

  @override
  ConsumerState<ActivityPage> createState() => _ActivityPageState();
}

class _ActivityPageState extends ConsumerState<ActivityPage> {
  @override
  void initState() {
    super.initState();
    Future.microtask(
      () => ref.read(activityControllerProvider.notifier).refresh(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(activityControllerProvider);
    return AgentPageScaffold(
      title: '活动',
      actions: <Widget>[
        IconButton(
          onPressed: () =>
              ref.read(activityControllerProvider.notifier).refresh(),
          icon: const Icon(Icons.refresh),
        ),
      ],
      body: Column(
        children: <Widget>[
          if (state.isLoading) const LinearProgressIndicator(minHeight: 2),
          if (state.error != null)
            Padding(
              padding: const EdgeInsets.all(12),
              child: ErrorBanner(message: state.error!),
            ),
          Expanded(
            child: state.items.isEmpty && !state.isLoading
                ? const EmptyState(
                    icon: Icons.bolt_outlined,
                    title: '暂无活动',
                    description: '实时事件和执行记录会显示在这里。',
                  )
                : ListView.separated(
                    padding: const EdgeInsets.all(16),
                    itemCount: state.items.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final item = state.items[index];
                      return ListTile(
                        leading: CircleAvatar(child: Icon(_iconFor(item.type))),
                        title: Text(item.title),
                        subtitle: Text(
                          '${item.description.isEmpty ? item.type : item.description}\n'
                          '${formatDateTime(item.createdAt)}',
                        ),
                        isThreeLine: true,
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  IconData _iconFor(String type) {
    if (type.startsWith('goal.')) return Icons.flag_outlined;
    if (type.startsWith('task.')) return Icons.task_alt_outlined;
    if (type.startsWith('approval.')) return Icons.approval_outlined;
    if (type.startsWith('memory.')) return Icons.psychology_outlined;
    if (type.startsWith('schedule.')) return Icons.calendar_month_outlined;
    return Icons.bolt_outlined;
  }
}
