String twoDigits(int value) => value.toString().padLeft(2, '0');

String formatDateTime(DateTime? value) {
  if (value == null) return '时间未知';
  final local = value.toLocal();
  return '${local.year}-${twoDigits(local.month)}-${twoDigits(local.day)} '
      '${twoDigits(local.hour)}:${twoDigits(local.minute)}';
}

String formatTime(DateTime value) =>
    '${twoDigits(value.hour)}:${twoDigits(value.minute)}';

String goalTitle(String input) {
  final firstLine = input.trim().split(RegExp(r'\r?\n')).first;
  if (firstLine.length <= 32) return firstLine;
  return '${firstLine.substring(0, 32)}…';
}

String statusLabel(String status) {
  const labels = <String, String>{
    'queued': '排队中',
    'pending': '待处理',
    'running': '执行中',
    'active': '执行中',
    'completed': '已完成',
    'succeeded': '已完成',
    'failed': '失败',
    'cancelled': '已取消',
    'canceled': '已取消',
    'retrying': '重试中',
    'approved': '已批准',
    'rejected': '已拒绝',
  };
  return labels[status.toLowerCase()] ?? status;
}
