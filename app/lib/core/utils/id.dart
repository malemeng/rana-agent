import 'dart:math';

String createId(String prefix) {
  final random = Random.secure();
  final suffix = List<int>.generate(
    12,
    (_) => random.nextInt(36),
  ).map((value) => value.toRadixString(36)).join();
  return '${prefix}_${DateTime.now().microsecondsSinceEpoch}_$suffix';
}

String createIdempotencyKey() => createId('goal');
