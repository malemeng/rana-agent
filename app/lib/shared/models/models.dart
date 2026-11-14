Map<String, dynamic> asJsonMap(dynamic value) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((key, dynamic item) => MapEntry(key.toString(), item));
  }
  return <String, dynamic>{};
}

List<Map<String, dynamic>> asJsonList(dynamic value) {
  if (value is List) {
    return value.map(asJsonMap).toList(growable: false);
  }
  if (value is Map) {
    final map = asJsonMap(value);
    for (final key in <String>[
      'items',
      'data',
      'results',
      'memories',
      'tasks',
    ]) {
      final candidate = map[key];
      if (candidate is List) return asJsonList(candidate);
    }
    final data = map['data'];
    if (data is Map && !identical(data, value)) return asJsonList(data);
  }
  return const <Map<String, dynamic>>[];
}

String jsonString(
  Map<String, dynamic> json,
  List<String> keys, [
  String fallback = '',
]) {
  for (final key in keys) {
    final value = json[key];
    if (value != null && value.toString().isNotEmpty) return value.toString();
  }
  return fallback;
}

DateTime? jsonDate(dynamic value) {
  if (value == null) return null;
  if (value is int) return DateTime.fromMillisecondsSinceEpoch(value);
  return DateTime.tryParse(value.toString());
}

bool jsonBool(dynamic value, {bool fallback = false}) {
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) return value.toLowerCase() == 'true';
  return fallback;
}

class AuthTokens {
  const AuthTokens({
    required this.accessToken,
    required this.refreshToken,
    required this.expiresIn,
  });

  factory AuthTokens.fromJson(Map<String, dynamic> json) => AuthTokens(
    accessToken: jsonString(json, <String>['access_token']),
    refreshToken: jsonString(json, <String>['refresh_token']),
    expiresIn: (json['expires_in'] as num?)?.toInt() ?? 3600,
  );

  final String accessToken;
  final String refreshToken;
  final int expiresIn;
}

class Goal {
  const Goal({
    required this.id,
    required this.title,
    required this.input,
    required this.status,
    this.idempotencyKey,
    this.createdAt,
    this.updatedAt,
    this.error,
  });

  factory Goal.fromJson(Map<String, dynamic> json) {
    final nested = json['goal'] is Map ? asJsonMap(json['goal']) : json;
    final input = jsonString(nested, <String>['input', 'description']);
    return Goal(
      id: jsonString(nested, <String>['id', 'goal_id']),
      title: jsonString(nested, <String>['title'], input),
      input: input,
      status: jsonString(nested, <String>['status'], 'queued'),
      idempotencyKey: nested['idempotency_key']?.toString(),
      createdAt: jsonDate(nested['created_at']),
      updatedAt: jsonDate(nested['updated_at']),
      error: nested['error']?.toString(),
    );
  }

  final String id;
  final String title;
  final String input;
  final String status;
  final String? idempotencyKey;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final String? error;

  Goal copyWith({String? status, DateTime? updatedAt, String? error}) => Goal(
    id: id,
    title: title,
    input: input,
    status: status ?? this.status,
    idempotencyKey: idempotencyKey,
    createdAt: createdAt,
    updatedAt: updatedAt ?? this.updatedAt,
    error: error ?? this.error,
  );

  Map<String, dynamic> toJson() => <String, dynamic>{
    'id': id,
    'title': title,
    'input': input,
    'status': status,
    'idempotency_key': idempotencyKey,
    'created_at': createdAt?.toIso8601String(),
    'updated_at': updatedAt?.toIso8601String(),
    'error': error,
  };
}

class AgentTask {
  const AgentTask({
    required this.id,
    required this.goalId,
    required this.title,
    required this.status,
    this.createdAt,
    this.updatedAt,
    this.error,
  });

  factory AgentTask.fromJson(Map<String, dynamic> json) => AgentTask(
    id: jsonString(json, <String>['id', 'task_id']),
    goalId: jsonString(json, <String>['goal_id', 'goalId']),
    title: jsonString(json, <String>['title', 'name'], '未命名任务'),
    status: jsonString(json, <String>['status'], 'pending'),
    createdAt: jsonDate(json['created_at']),
    updatedAt: jsonDate(json['updated_at']),
    error: json['error']?.toString(),
  );

  final String id;
  final String goalId;
  final String title;
  final String status;
  final DateTime? createdAt;
  final DateTime? updatedAt;
  final String? error;

  AgentTask copyWith({String? status, String? error}) => AgentTask(
    id: id,
    goalId: goalId,
    title: title,
    status: status ?? this.status,
    createdAt: createdAt,
    updatedAt: DateTime.now(),
    error: error ?? this.error,
  );
}

class ApprovalItem {
  const ApprovalItem({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    this.createdAt,
    this.payload = const <String, dynamic>{},
  });

  factory ApprovalItem.fromJson(Map<String, dynamic> json) => ApprovalItem(
    id: jsonString(json, <String>['id', 'approval_id']),
    title: jsonString(json, <String>['title', 'name', 'action'], '待审批操作'),
    description: jsonString(json, <String>[
      'description',
      'detail',
      'reason',
    ], asJsonMap(json['payload_json'] ?? json['payload']).toString()),
    status: jsonString(json, <String>['status'], 'pending'),
    createdAt: jsonDate(json['created_at']),
    payload: asJsonMap(json['payload_json'] ?? json['payload']),
  );

  final String id;
  final String title;
  final String description;
  final String status;
  final DateTime? createdAt;
  final Map<String, dynamic> payload;

  ApprovalItem copyWith({String? status}) => ApprovalItem(
    id: id,
    title: title,
    description: description,
    status: status ?? this.status,
    createdAt: createdAt,
    payload: payload,
  );
}

class ActivityItem {
  const ActivityItem({
    required this.id,
    required this.type,
    required this.title,
    required this.description,
    this.createdAt,
  });

  factory ActivityItem.fromJson(Map<String, dynamic> json) {
    final type = jsonString(json, <String>['type', 'event'], 'activity');
    final payload = asJsonMap(json['payload_json'] ?? json['payload']);
    return ActivityItem(
      id: jsonString(json, <String>['id', 'event_id']),
      type: type,
      title: jsonString(json, <String>['title', 'message'], type),
      description: jsonString(json, <String>[
        'description',
        'detail',
        'body',
      ], payload.toString()),
      createdAt: jsonDate(json['created_at'] ?? json['timestamp']),
    );
  }

  final String id;
  final String type;
  final String title;
  final String description;
  final DateTime? createdAt;
}

class MemoryItem {
  const MemoryItem({
    required this.id,
    required this.content,
    this.tags = const <String>[],
    this.createdAt,
    this.updatedAt,
  });

  factory MemoryItem.fromJson(Map<String, dynamic> json) => MemoryItem(
    id: jsonString(json, <String>['id', 'memory_id']),
    content: jsonString(json, <String>['content', 'text', 'memory']),
    tags: (json['tags'] as List<dynamic>? ?? const <dynamic>[])
        .map((dynamic value) => value.toString())
        .toList(growable: false),
    createdAt: jsonDate(json['created_at']),
    updatedAt: jsonDate(json['updated_at']),
  );

  final String id;
  final String content;
  final List<String> tags;
  final DateTime? createdAt;
  final DateTime? updatedAt;

  MemoryItem copyWith({String? content, List<String>? tags}) => MemoryItem(
    id: id,
    content: content ?? this.content,
    tags: tags ?? this.tags,
    createdAt: createdAt,
    updatedAt: DateTime.now(),
  );
}

class ScheduleItem {
  const ScheduleItem({
    required this.id,
    required this.title,
    required this.prompt,
    required this.cron,
    this.timezone = 'Asia/Shanghai',
    this.enabled = true,
    this.nextRunAt,
    this.lastRunAt,
  });

  factory ScheduleItem.fromJson(Map<String, dynamic> json) => ScheduleItem(
    id: jsonString(json, <String>['id', 'schedule_id']),
    title: jsonString(json, <String>['title', 'name'], '定时任务'),
    prompt: jsonString(json, <String>['prompt', 'notes']),
    cron: jsonString(json, <String>['cron'], '0 9 * * *'),
    timezone: jsonString(json, <String>['timezone'], 'Asia/Shanghai'),
    enabled: jsonBool(json['enabled'], fallback: true),
    nextRunAt: jsonDate(json['next_run_at']),
    lastRunAt: jsonDate(json['last_run_at']),
  );

  final String id;
  final String title;
  final String prompt;
  final String cron;
  final String timezone;
  final bool enabled;
  final DateTime? nextRunAt;
  final DateTime? lastRunAt;

  ScheduleItem copyWith({bool? enabled}) => ScheduleItem(
    id: id,
    title: title,
    prompt: prompt,
    cron: cron,
    timezone: timezone,
    enabled: enabled ?? this.enabled,
    nextRunAt: nextRunAt,
    lastRunAt: lastRunAt,
  );
}

enum MessageStatus { sending, streaming, sent, failed }

class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.role,
    required this.content,
    required this.createdAt,
    this.status = MessageStatus.sent,
    this.goalId,
    this.taskId,
    this.conversationId,
  });

  final String id;
  final String role;
  final String content;
  final DateTime createdAt;
  final MessageStatus status;
  final String? goalId;
  final String? taskId;
  final String? conversationId;

  ChatMessage copyWith({
    String? content,
    MessageStatus? status,
    String? goalId,
    String? taskId,
    String? conversationId,
  }) => ChatMessage(
    id: id,
    role: role,
    content: content ?? this.content,
    createdAt: createdAt,
    status: status ?? this.status,
    goalId: goalId ?? this.goalId,
    taskId: taskId ?? this.taskId,
    conversationId: conversationId ?? this.conversationId,
  );
}

class ServerEvent {
  const ServerEvent({required this.type, required this.data, this.activityId});

  factory ServerEvent.fromJson(Map<String, dynamic> json) {
    final payload = json['payload'] ?? json['data'];
    final data = payload is Map
        ? <String, dynamic>{...asJsonMap(payload)}
        : <String, dynamic>{...json};
    for (final key in <String>[
      'goal_id',
      'task_id',
      'conversation_id',
      'created_at',
    ]) {
      if (!data.containsKey(key) && json.containsKey(key)) {
        data[key] = json[key];
      }
    }
    final rawId = json['id'];
    final activityId = rawId is num
        ? rawId.toInt()
        : int.tryParse(rawId?.toString() ?? '');
    data['event_id'] = rawId;
    return ServerEvent(
      type: jsonString(json, <String>['type', 'event'], 'unknown'),
      data: data,
      activityId: activityId,
    );
  }

  final String type;
  final Map<String, dynamic> data;
  final int? activityId;
}

class ConnectionResult {
  const ConnectionResult({required this.success, required this.message});

  factory ConnectionResult.fromJson(Map<String, dynamic> json) {
    final success = jsonBool(json['success'] ?? json['ok'], fallback: true);
    return ConnectionResult(
      success: success,
      message: jsonString(json, <String>[
        'message',
        'detail',
      ], success ? '连接成功' : '连接失败'),
    );
  }

  final bool success;
  final String message;
}
