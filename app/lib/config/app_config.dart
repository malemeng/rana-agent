class AppConfig {
  const AppConfig._();

  static const String defaultBaseUrl = 'http://10.0.2.2:8080/api/v1';
  static const int voiceSampleRate = 16000;

  static Uri eventsUri(String baseUrl, String accessToken, {int after = 0}) =>
      _webSocketUri(baseUrl, '/ws/events', accessToken, after: after);

  static Uri voiceUri(String baseUrl, String accessToken) =>
      _webSocketUri(baseUrl, '/ws/voice', accessToken);

  static Uri _webSocketUri(
    String baseUrl,
    String path,
    String accessToken, {
    int after = 0,
  }) {
    final httpUri = Uri.parse(normalizeBaseUrl(baseUrl));
    final scheme = httpUri.scheme == 'https' ? 'wss' : 'ws';
    var rootPath = httpUri.path;
    if (rootPath.endsWith('/api/v1')) {
      rootPath = rootPath.substring(0, rootPath.length - '/api/v1'.length);
    }
    final prefix = rootPath.endsWith('/') && rootPath.isNotEmpty
        ? rootPath.substring(0, rootPath.length - 1)
        : rootPath;
    return httpUri.replace(
      scheme: scheme,
      path: '$prefix$path',
      queryParameters: <String, String>{
        'token': accessToken,
        if (after > 0) 'after': after.toString(),
      },
      fragment: null,
    );
  }

  static String normalizeBaseUrl(String value) {
    final trimmed = value.trim();
    final withScheme = trimmed.contains('://') ? trimmed : 'http://$trimmed';
    return withScheme.endsWith('/')
        ? withScheme.substring(0, withScheme.length - 1)
        : withScheme;
  }
}
