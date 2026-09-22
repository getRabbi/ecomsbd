import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../app/providers.dart';
import '../../core/api/api_client.dart';
import 'commerce_providers.dart';

const crmSegments = [
  'NEW',
  'REPEAT',
  'HIGH_VALUE',
  'INACTIVE',
  'SUCCESSFUL_REPEAT',
  'REPEATED_RTO',
  'FOLLOW_UP_DUE',
  'ACTIVE_ORDERS',
];

class CrmRepository {
  CrmRepository(this.api);
  final ApiClient api;
  Future<Map<String, dynamic>> detail(String id) =>
      api.get('/customers/$id/crm');
  Future<Map<String, dynamic>> page(
    String path, {
    String? cursor,
    Map<String, dynamic> extra = const {},
  }) => api.get(
    path,
    query: {'limit': 20, if (cursor != null) 'cursor': cursor, ...extra},
  );
  Future<Map<String, dynamic>> add(String path, Map<String, dynamic> body) =>
      api.post(path, body: body);
  Future<void> complete(String customerId, String id, bool completed) =>
      api.patch(
        '/customers/$customerId/follow-ups/$id',
        body: {'completed': completed},
      );
  Future<void> tag(String customerId, String tagId, {bool remove = false}) =>
      api.post(
        '/customers/crm/bulk-tags',
        body: {
          'customer_ids': [customerId],
          'tag_id': tagId,
          'remove': remove,
        },
      );
}

final crmRepositoryProvider = Provider((ref) {
  ref.watch(tenantIdProvider);
  return CrmRepository(ref.watch(apiClientProvider));
});
final crmCustomerProvider = FutureProvider.autoDispose
    .family<Map<String, dynamic>, String>(
      (ref, id) => ref.watch(crmRepositoryProvider).detail(id),
    );
