import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/errors.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/models/models.dart';
import '../../../shared/widgets/common.dart';

class SchedulePage extends ConsumerStatefulWidget {
  const SchedulePage({super.key});

  @override
  ConsumerState<SchedulePage> createState() => _SchedulePageState();
}

class _SchedulePageState extends ConsumerState<SchedulePage> {
  @override
  void initState() {
    super.initState();
    Future.microtask(
      () => ref.read(scheduleControllerProvider.notifier).refresh(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(scheduleControllerProvider);
    return AgentPageScaffold(
      title: '日程',
      actions: <Widget>[
        IconButton(
          onPressed: () =>
              ref.read(scheduleControllerProvider.notifier).refresh(),
          icon: const Icon(Icons.refresh),
        ),
      ],
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _createSchedule,
        icon: const Icon(Icons.event_available_outlined),
        label: const Text('新增日程'),
      ),
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
                    icon: Icons.calendar_month_outlined,
                    title: '暂无日程',
                    description: '用 cron 创建周期性 Agent 提示与自动化任务。',
                  )
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 88),
                    itemCount: state.items.length,
                    itemBuilder: (context, index) {
                      final item = state.items[index];
                      return Card(
                        child: ListTile(
                          leading: Switch(
                            value: item.enabled,
                            onChanged: (value) => ref
                                .read(scheduleControllerProvider.notifier)
                                .setEnabled(item, value),
                          ),
                          title: Text(item.title),
                          subtitle: Text(
                            '${item.cron} · ${item.timezone}\n'
                            '下次运行：${formatDateTime(item.nextRunAt)}'
                            '${item.prompt.isEmpty ? '' : '\n${item.prompt}'}',
                          ),
                          isThreeLine: true,
                          trailing: IconButton(
                            onPressed: () => ref
                                .read(scheduleControllerProvider.notifier)
                                .delete(item.id),
                            icon: const Icon(Icons.delete_outline),
                            tooltip: '删除日程',
                          ),
                        ),
                      );
                    },
                  ),
          ),
        ],
      ),
    );
  }

  Future<void> _createSchedule() async {
    final title = TextEditingController();
    final prompt = TextEditingController();
    final cron = TextEditingController(text: '0 9 * * *');
    final timezone = TextEditingController(text: 'Asia/Shanghai');
    final draft = await showDialog<_ScheduleDraft>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('新增 cron 日程'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              TextField(
                controller: title,
                autofocus: true,
                decoration: const InputDecoration(labelText: '标题'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: prompt,
                minLines: 2,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: 'Agent 提示',
                  hintText: '例如：总结今天的重要日程与未完成任务',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: cron,
                decoration: const InputDecoration(
                  labelText: 'cron 表达式',
                  helperText: '分 时 日 月 周，例如 0 9 * * *',
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: timezone,
                decoration: const InputDecoration(
                  labelText: 'IANA 时区',
                  hintText: 'Asia/Shanghai',
                ),
              ),
            ],
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(
              context,
              _ScheduleDraft(title.text, prompt.text, cron.text, timezone.text),
            ),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    title.dispose();
    prompt.dispose();
    cron.dispose();
    timezone.dispose();
    if (draft == null || !mounted) return;
    if (draft.title.trim().isEmpty ||
        draft.prompt.trim().isEmpty ||
        draft.cron.trim().isEmpty ||
        draft.timezone.trim().isEmpty) {
      return;
    }
    try {
      await ref
          .read(scheduleControllerProvider.notifier)
          .create(
            ScheduleItem(
              id: '',
              title: draft.title.trim(),
              prompt: draft.prompt.trim(),
              cron: draft.cron.trim(),
              timezone: draft.timezone.trim(),
            ),
          );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(readableError(error))));
      }
    }
  }
}

class _ScheduleDraft {
  const _ScheduleDraft(this.title, this.prompt, this.cron, this.timezone);

  final String title;
  final String prompt;
  final String cron;
  final String timezone;
}
