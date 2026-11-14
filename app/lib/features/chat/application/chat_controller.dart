import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../../core/utils/errors.dart';
import '../../../core/utils/id.dart';
import '../../../shared/models/models.dart';
import '../data/conversation_repository.dart';

class ChatState {
  const ChatState({
    this.messages = const <ChatMessage>[],
    this.isSending = false,
    this.error,
  });

  final List<ChatMessage> messages;
  final bool isSending;
  final String? error;

  ChatState copyWith({
    List<ChatMessage>? messages,
    bool? isSending,
    String? error,
    bool clearError = false,
  }) => ChatState(
    messages: messages ?? this.messages,
    isSending: isSending ?? this.isSending,
    error: clearError ? null : error ?? this.error,
  );
}

class ChatController extends StateNotifier<ChatState> {
  ChatController({required ConversationRepository repository})
    : _repository = repository,
      super(
        ChatState(
          messages: <ChatMessage>[
            ChatMessage(
              id: createId('welcome'),
              role: 'assistant',
              content: '你好，我是你的 Rana Agent。请告诉我你想完成什么目标。',
              createdAt: DateTime.now(),
            ),
          ],
        ),
      ) {
    unawaited(_restoreLatest());
  }

  final ConversationRepository _repository;
  bool _hasLocalActivity = false;

  Future<void> _restoreLatest() async {
    try {
      final history = await _repository.restoreLatest();
      if (!mounted || history.isEmpty) return;
      if (!_hasLocalActivity) {
        state = state.copyWith(messages: history, clearError: true);
        return;
      }
      final existingIds = state.messages.map((message) => message.id).toSet();
      state = state.copyWith(
        messages: <ChatMessage>[
          ...history.where((message) => !existingIds.contains(message.id)),
          ...state.messages,
        ],
      );
    } catch (error) {
      if (mounted && !_hasLocalActivity) {
        state = state.copyWith(error: readableError(error));
      }
    }
  }

  Future<void> send(String rawInput) async {
    final input = rawInput.trim();
    if (input.isEmpty || state.isSending) return;
    _hasLocalActivity = true;
    final userMessage = ChatMessage(
      id: createId('message'),
      role: 'user',
      content: input,
      createdAt: DateTime.now(),
      status: MessageStatus.sending,
    );
    state = state.copyWith(
      messages: <ChatMessage>[...state.messages, userMessage],
      isSending: true,
      clearError: true,
    );
    try {
      final result = await _repository.send(input);
      if (!mounted) return;
      final pending = ChatMessage(
        id: result.taskId.isEmpty
            ? createId('stream')
            : 'task_${result.taskId}',
        role: 'assistant',
        content: '',
        createdAt: DateTime.now(),
        status: MessageStatus.streaming,
        taskId: result.taskId,
        conversationId: result.conversationId,
      );
      state = state.copyWith(
        messages: <ChatMessage>[
          ...state.messages.map(
            (message) => message.id == userMessage.id
                ? message.copyWith(
                    status: MessageStatus.sent,
                    conversationId: result.conversationId,
                  )
                : message,
          ),
          pending,
        ],
        isSending: false,
      );
    } catch (error) {
      if (!mounted) return;
      final messages = state.messages
          .map((message) {
            return message.id == userMessage.id
                ? message.copyWith(status: MessageStatus.failed)
                : message;
          })
          .toList(growable: false);
      state = state.copyWith(
        messages: messages,
        isSending: false,
        error: readableError(error),
      );
    }
  }

  void addTranscript(String role, String text) {
    if (text.trim().isEmpty) return;
    _hasLocalActivity = true;
    state = state.copyWith(
      messages: <ChatMessage>[
        ...state.messages,
        ChatMessage(
          id: createId('voice'),
          role: role,
          content: text,
          createdAt: DateTime.now(),
        ),
      ],
    );
  }

  void handleEvent(ServerEvent event) {
    final type = event.type;
    final isDelta =
        type == 'message.delta' ||
        type == 'chat.delta' ||
        type == 'response.delta';
    final isDone =
        type == 'message.completed' ||
        type == 'message.done' ||
        type == 'chat.completed' ||
        type == 'response.completed' ||
        type == 'agent.completed';
    if (!isDelta && !isDone) return;
    _hasLocalActivity = true;
    final nested = event.data['message'];
    final data = nested is Map ? asJsonMap(nested) : event.data;
    final messageId = jsonString(data, <String>['message_id', 'id']);
    final taskId = data['task_id']?.toString();
    final goalId = data['goal_id']?.toString();
    final conversationId = data['conversation_id']?.toString();
    final text = _eventText(data);
    final messages = <ChatMessage>[...state.messages];
    var index = messageId.isEmpty
        ? -1
        : messages.indexWhere((message) => message.id == messageId);
    if (index < 0 && taskId != null && taskId.isNotEmpty) {
      index = messages.lastIndexWhere(
        (message) => message.taskId == taskId && message.role == 'assistant',
      );
    }
    if (index < 0 && goalId != null) {
      index = messages.lastIndexWhere(
        (message) =>
            message.goalId == goalId &&
            message.role == 'assistant' &&
            message.status == MessageStatus.streaming,
      );
    }
    if (index < 0) {
      index = messages.lastIndexWhere(
        (message) =>
            message.role == 'assistant' &&
            message.status == MessageStatus.streaming,
      );
    }
    if (index < 0) {
      if (isDone && text.isEmpty) return;
      messages.add(
        ChatMessage(
          id: messageId.isNotEmpty
              ? messageId
              : 'stream_${taskId ?? conversationId ?? goalId ?? createId('event')}',
          role: 'assistant',
          content: text,
          createdAt: DateTime.now(),
          goalId: goalId,
          taskId: taskId,
          conversationId: conversationId,
          status: isDone ? MessageStatus.sent : MessageStatus.streaming,
        ),
      );
    } else {
      final current = messages[index];
      messages[index] = current.copyWith(
        content: isDone && text.isNotEmpty ? text : '${current.content}$text',
        status: isDone ? MessageStatus.sent : MessageStatus.streaming,
        conversationId: conversationId,
      );
    }
    state = state.copyWith(messages: messages);
  }

  String _eventText(Map<String, dynamic> data) {
    final direct = jsonString(data, <String>['delta', 'content', 'text']);
    if (direct.isNotEmpty) return direct;
    for (final key in <String>['result', 'result_json', 'output']) {
      final value = data[key];
      if (value is String) return value;
      if (value is Map) {
        final map = asJsonMap(value);
        final text = jsonString(map, <String>[
          'text',
          'content',
          'output',
          'answer',
        ]);
        if (text.isNotEmpty) return text;
      }
    }
    return '';
  }
}
