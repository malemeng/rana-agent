import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/errors.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/models/models.dart';
import '../../../shared/widgets/common.dart';

class MemoriesPage extends ConsumerStatefulWidget {
  const MemoriesPage({super.key});

  @override
  ConsumerState<MemoriesPage> createState() => _MemoriesPageState();
}

class _MemoriesPageState extends ConsumerState<MemoriesPage> {
  @override
  void initState() {
    super.initState();
    Future.microtask(
      () => ref.read(memoryControllerProvider.notifier).refresh(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(memoryControllerProvider);
    return AgentPageScaffold(
      title: '记忆',
      actions: <Widget>[
        IconButton(
          onPressed: () =>
              ref.read(memoryControllerProvider.notifier).refresh(),
          icon: const Icon(Icons.refresh),
        ),
      ],
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _editMemory(),
        icon: const Icon(Icons.add),
        label: const Text('新增记忆'),
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
                    icon: Icons.psychology_outlined,
                    title: '暂无记忆',
                    description: '新增偏好、事实或长期上下文。',
                  )
                : ListView.builder(
                    padding: const EdgeInsets.fromLTRB(16, 16, 16, 88),
                    itemCount: state.items.length,
                    itemBuilder: (context, index) {
                      final item = state.items[index];
                      return Card(
                        child: ListTile(
                          title: Text(item.content),
                          subtitle: Text(
                            '${item.tags.map((tag) => '#$tag').join(' ')}\n'
                            '${formatDateTime(item.updatedAt ?? item.createdAt)}',
                          ),
                          isThreeLine: true,
                          onTap: () => _editMemory(item),
                          trailing: state.busyIds.contains(item.id)
                              ? const CircularProgressIndicator()
                              : IconButton(
                                  onPressed: () => ref
                                      .read(memoryControllerProvider.notifier)
                                      .delete(item.id),
                                  icon: const Icon(Icons.delete_outline),
                                  tooltip: '删除记忆',
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

  Future<void> _editMemory([MemoryItem? item]) async {
    final content = TextEditingController(text: item?.content);
    final tags = TextEditingController(text: item?.tags.join(', '));
    final saved = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(item == null ? '新增记忆' : '编辑记忆'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            TextField(
              controller: content,
              autofocus: true,
              minLines: 3,
              maxLines: 6,
              decoration: const InputDecoration(labelText: '记忆内容'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: tags,
              decoration: const InputDecoration(
                labelText: '标签',
                helperText: '多个标签用逗号分隔',
              ),
            ),
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (saved != true || content.text.trim().isEmpty) {
      content.dispose();
      tags.dispose();
      return;
    }
    try {
      await ref
          .read(memoryControllerProvider.notifier)
          .save(
            id: item?.id,
            content: content.text.trim(),
            tags: tags.text
                .split(RegExp(r'[,，]'))
                .map((tag) => tag.trim())
                .where((tag) => tag.isNotEmpty)
                .toList(growable: false),
          );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(readableError(error))));
      }
    }
    content.dispose();
    tags.dispose();
  }
}
