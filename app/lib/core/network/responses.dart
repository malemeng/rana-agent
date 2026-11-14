import '../../shared/models/models.dart';

Map<String, dynamic> unwrapEntity(dynamic data, [String? key]) {
  final body = asJsonMap(data);
  if (key != null && body[key] is Map) return asJsonMap(body[key]);
  if (body['data'] is Map) return unwrapEntity(body['data'], key);
  return body;
}
