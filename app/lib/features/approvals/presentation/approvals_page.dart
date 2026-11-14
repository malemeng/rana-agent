import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/models/models.dart';
import '../../../shared/widgets/common.dart';

class ApprovalsPage extends ConsumerStatefulWidget {
  const ApprovalsPage({super.key});

  @override
  ConsumerState<ApprovalsPage> createState() => _ApprovalsPageState();
}

class _ApprovalsPageState extends ConsumerState<ApprovalsPage> {
  @override
  void initState() {
    super.initState();
    Future.microtask(
      () => ref.read(approvalControllerProvider.notifier).refresh(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(approvalControllerProvider);
    return AgentPageScaffold(
      title: '审批',
      actions: <Widget>[
        IconButton(
          onPressed: () =>
              ref.read(approvalControllerProvider.notifier).refresh(),
          icon: const Icon(Icons.refresh),
        ),
      ],
      body: Column(
        children: <Widget>[
          if (state.isLoading) const LinearProgressIndicator(minHeight: 2),
          if (state.error != null)
            Padding(
              padding: const EdgeInsets.all(12),
              child: ErrorBanner(
                message: state.error!,
                onRetry: () =>
                    ref.read(approvalControllerProvider.notifier).refresh(),
              ),
            ),
          Expanded(
            child: state.items.isEmpty && !state.isLoading
                ? const EmptyState(
                    icon: Icons.verified_user_outlined,
                    title: '没有待审批事项',
                    description: '需要人工确认的操作会出现在这里。',
                  )
                : ListView.builder(
                    padding: const EdgeInsets.all(16),
                    itemCount: state.items.length,
                    itemBuilder: (context, index) =>
                        _ApprovalCard(item: state.items[index]),
                  ),
          ),
        ],
      ),
    );
  }
}

class _ApprovalCard extends ConsumerWidget {
  const _ApprovalCard({required this.item});

  final ApprovalItem item;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(approvalControllerProvider);
    final busy = state.busyIds.contains(item.id);
    final pending = item.status.toLowerCase() == 'pending';
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
                    item.title,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                Chip(label: Text(statusLabel(item.status))),
              ],
            ),
            if (item.description.isNotEmpty) Text(item.description),
            const SizedBox(height: 8),
            Text(formatDateTime(item.createdAt)),
            if (busy) const LinearProgressIndicator(minHeight: 2),
            if (pending)
              OverflowBar(
                children: <Widget>[
                  OutlinedButton.icon(
                    onPressed: busy
                        ? null
                        : () => ref
                              .read(approvalControllerProvider.notifier)
                              .reject(item.id),
                    icon: const Icon(Icons.close),
                    label: const Text('拒绝'),
                  ),
                  FilledButton.icon(
                    onPressed: busy
                        ? null
                        : () => ref
                              .read(approvalControllerProvider.notifier)
                              .approve(item.id),
                    icon: const Icon(Icons.check),
                    label: const Text('批准'),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
