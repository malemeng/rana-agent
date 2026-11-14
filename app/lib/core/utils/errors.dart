import 'package:dio/dio.dart';

String readableError(Object error) {
  if (error is DioException) {
    final data = error.response?.data;
    if (data is Map) {
      final map = Map<String, dynamic>.from(data);
      final detail = map['detail'] ?? map['message'] ?? map['error'];
      if (detail != null) return detail.toString();
    }
    if (data is String && data.trim().isNotEmpty) return data;
    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.transformTimeout:
        return '连接服务器超时，请检查地址与网络';
      case DioExceptionType.connectionError:
        return '无法连接服务器，请检查 Base URL';
      case DioExceptionType.badResponse:
        return '服务器返回 ${error.response?.statusCode ?? '未知'} 错误';
      case DioExceptionType.cancel:
        return '请求已取消';
      case DioExceptionType.badCertificate:
        return '服务器证书校验失败';
      case DioExceptionType.unknown:
        return error.message ?? '网络请求失败';
    }
  }
  return error.toString().replaceFirst('Exception: ', '');
}
