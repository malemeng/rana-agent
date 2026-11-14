import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../app/providers.dart';
import '../../../core/utils/formatters.dart';
import '../../../shared/models/models.dart';
import '../../../shared/widgets/common.dart';
import '../../voice/application/voice_controller.dart';
import '../../voice/presentation/voice_button.dart';

class ChatPage extends ConsumerStatefulWidget {
  const ChatPage({super.key});

  @override
  ConsumerState<ChatPage> createState() => _ChatPageState();
}

class _ChatPageState extends ConsumerState<ChatPage> {
  final _input = TextEditingController();

  @override
  void dispose() {
    _input.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final text = _input.text;
    if (text.trim().isEmpty) return;
    _input.clear();
    await ref.read(chatControllerProvider.notifier).send(text);
  }

  @override
  Widget build(BuildContext context) {
    ref.listen<VoiceState>(voiceControllerProvider, (previous, next) {
      if (next.phase == VoicePhase.error &&
          next.error != null &&
          next.error != previous?.error) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(next.error!)));
      }
    });
    final chat = ref.watch(chatControllerProvider);
    return AgentPageScaffold(
      title: '主对话',
      body: Column(
        children: <Widget>[
          Expanded(
            child: ListView.separated(
              reverse: true,
              padding: const EdgeInsets.fromLTRB(16, 20, 16, 12),
              itemCount: chat.messages.length,
              separatorBuilder: (_, __) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                final message = chat.messages[chat.messages.length - 1 - index];
                return _MessageBubble(message: message);
              },
            ),
          ),
          if (chat.isSending) const LinearProgressIndicator(minHeight: 2),
          if (chat.error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: ErrorBanner(message: chat.error!),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: <Widget>[
                const VoiceButton(),
                const SizedBox(width: 8),
                Expanded(
                  child: TextField(
                    key: const Key('chatInput'),
                    controller: _input,
                    minLines: 1,
                    maxLines: 5,
                    textCapitalization: TextCapitalization.sentences,
                    decoration: const InputDecoration(
                      hintText: '输入目标或问题…',
                      filled: true,
                    ),
                    onSubmitted: (_) => _send(),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  key: const Key('sendButton'),
                  onPressed: chat.isSending ? null : _send,
                  icon: const Icon(Icons.arrow_upward),
                  tooltip: '创建目标并发送',
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _MessageBubble extends StatelessWidget {
  const _MessageBubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final isUser = message.role == 'user';
    final scheme = Theme.of(context).colorScheme;
    final color = isUser
        ? scheme.primaryContainer
        : scheme.surfaceContainerHighest;
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 680),
        child: FractionallySizedBox(
          widthFactor: 0.88,
          child: Align(
            alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
            child: Card(
              color: color,
              elevation: 0,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      isUser ? '你' : 'Rana Agent',
                      style: Theme.of(context).textTheme.labelMedium,
                    ),
                    const SizedBox(height: 6),
                    SelectableText(message.content),
                    if (message.status == MessageStatus.streaming) ...<Widget>[
                      const SizedBox(height: 8),
                      const SizedBox(
                        width: 48,
                        child: LinearProgressIndicator(minHeight: 2),
                      ),
                    ],
                    if (message.status == MessageStatus.failed)
                      const Text('发送失败', style: TextStyle(color: Colors.red)),
                    const SizedBox(height: 4),
                    Text(
                      formatDateTime(message.createdAt),
                      style: Theme.of(context).textTheme.labelSmall,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
