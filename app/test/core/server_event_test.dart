import 'package:flutter_test/flutter_test.dart';
import 'package:personal_agent/shared/models/models.dart';

void main() {
  test('事件 payload 与 envelope 分离并保留持久游标', () {
    final event = ServerEvent.fromJson(<String, dynamic>{
      'id': 42,
      'goal_id': null,
      'task_id': 'task-1',
      'type': 'message.delta',
      'payload': <String, dynamic>{'delta': '你好'},
      'created_at': '2026-01-01T00:00:00Z',
    });

    expect(event.activityId, 42);
    expect(event.data['event_id'], 42);
    expect(event.data['task_id'], 'task-1');
    expect(event.data['delta'], '你好');
    expect(event.data['id'], isNull);
  });

  test('瞬态事件不伪造持久游标', () {
    final event = ServerEvent.fromJson(<String, dynamic>{
      'id': null,
      'type': 'message.delta',
      'payload': <String, dynamic>{'delta': 'A'},
    });
    expect(event.activityId, isNull);
    expect(event.data['delta'], 'A');
  });
}
