import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/web_socket_channel.dart';

import '../../shared/models/models.dart';

abstract class EventsTransport {
  int? get closeCode;
  Future<Stream<ServerEvent>> connect(Uri uri);
  Future<void> close();
}

class WebSocketEventsTransport implements EventsTransport {
  WebSocketChannel? _channel;

  @override
  int? get closeCode => _channel?.closeCode;

  @override
  Future<Stream<ServerEvent>> connect(Uri uri) async {
    final channel = WebSocketChannel.connect(uri);
    _channel = channel;
    try {
      await channel.ready;
    } catch (_) {
      _channel = null;
      rethrow;
    }
    return channel.stream.map((dynamic data) {
      final raw = data is String ? data : utf8.decode(data as List<int>);
      return ServerEvent.fromJson(asJsonMap(jsonDecode(raw)));
    });
  }

  @override
  Future<void> close() async {
    await _channel?.sink.close();
    _channel = null;
  }
}
