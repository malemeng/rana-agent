import 'package:dio/dio.dart';

import '../storage/token_storage.dart';

BaseOptions _baseOptions(String baseUrl) => BaseOptions(
  baseUrl: baseUrl,
  connectTimeout: const Duration(seconds: 15),
  receiveTimeout: const Duration(seconds: 45),
  sendTimeout: const Duration(seconds: 15),
  headers: <String, dynamic>{'Content-Type': 'application/json'},
);

class ApiClient {
  ApiClient({required String baseUrl, required TokenStorage tokenStorage})
    : _tokenStorage = tokenStorage,
      dio = Dio(_baseOptions(baseUrl)),
      authDio = Dio(_baseOptions(baseUrl)) {
    dio.interceptors.add(
      _AuthInterceptor(dio: dio, tokenStorage: _tokenStorage, owner: this),
    );
  }

  final Dio dio;
  final Dio authDio;
  final TokenStorage _tokenStorage;
  Future<bool>? _refreshFuture;

  Future<bool> Function()? refreshHandler;
  void Function()? sessionExpiredHandler;

  String get baseUrl => dio.options.baseUrl;

  void updateBaseUrl(String value) {
    dio.options.baseUrl = value;
    authDio.options.baseUrl = value;
  }

  Future<bool> refreshSession() => _refreshFuture ??= _refreshAndClear();

  Future<bool> _refreshAndClear() async {
    try {
      return await refreshHandler!();
    } finally {
      _refreshFuture = null;
    }
  }

  void close() {
    dio.close();
    authDio.close();
  }
}

class _AuthInterceptor extends Interceptor {
  _AuthInterceptor({
    required this.dio,
    required this.tokenStorage,
    required this.owner,
  });

  final Dio dio;
  final TokenStorage tokenStorage;
  final ApiClient owner;

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    if (options.extra['skipAuth'] != true) {
      final token = await tokenStorage.readAccessToken();
      if (token != null && token.isNotEmpty) {
        options.headers['Authorization'] = 'Bearer $token';
      }
    } else {
      options.headers.remove('Authorization');
    }
    handler.next(options);
  }

  @override
  Future<void> onError(
    DioException err,
    ErrorInterceptorHandler handler,
  ) async {
    final options = err.requestOptions;
    final shouldRefresh =
        err.response?.statusCode == 401 &&
        options.extra['skipRefresh'] != true &&
        options.extra['retried'] != true &&
        owner.refreshHandler != null;
    if (!shouldRefresh) {
      handler.next(err);
      return;
    }

    try {
      final refreshed = await owner.refreshSession();
      if (!refreshed) {
        owner.sessionExpiredHandler?.call();
        handler.next(err);
        return;
      }
      options.extra['retried'] = true;
      final accessToken = await tokenStorage.readAccessToken();
      options.headers['Authorization'] = 'Bearer $accessToken';
      final response = await dio.fetch<dynamic>(options);
      handler.resolve(response);
    } on DioException catch (retryError) {
      handler.next(retryError);
    } catch (error) {
      handler.next(
        DioException(requestOptions: options, error: error, message: '$error'),
      );
    }
  }
}
