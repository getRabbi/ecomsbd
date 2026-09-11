import '../../core/api/api_client.dart';
import 'models.dart';

/// Courier integration API access.
///
/// Deliberately **not** a `CachingRepository`. Every method here either touches
/// a courier or reports what one said, and a cached answer would be worse than
/// no answer:
///
/// * a cached "connected" would let a seller start a booking against a key the
///   courier has since revoked;
/// * a cached booking outcome is the one thing in this product that must never
///   be guessed;
/// * a cached parcel status would show "delivered" for a parcel that came back.
///
/// So reads go to the network and failures surface as [ApiError] for the screen
/// to render, rather than being papered over with yesterday's truth.
class CourierRepository {
  CourierRepository({required this.api});

  final ApiClient api;

  // --- accounts -------------------------------------------------------------

  Future<List<CourierAccount>> accounts() async {
    final rows = await api.getList('/couriers/accounts');
    return <CourierAccount>[
      for (final row in rows)
        CourierAccount.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Save credentials for a provider.
  ///
  /// The key and secret leave the device once, over TLS, and are never read
  /// back: the response carries a masked identifier and nothing else.
  Future<ConnectionTestResult> connect({
    required String provider,
    required String apiKey,
    required String secretKey,
    String? label,
  }) async {
    final json = await api.post(
      '/couriers/accounts/$provider/connect',
      body: <String, dynamic>{
        'api_key': apiKey,
        'secret_key': secretKey,
        if (label != null && label.isNotEmpty) 'label': label,
      },
    );
    return ConnectionTestResult.fromJson(json);
  }

  /// Re-check stored credentials against the courier.
  ///
  /// Uses the safest read the provider offers; no parcel is created to test a
  /// key.
  Future<ConnectionTestResult> testConnection(String provider) async {
    final json = await api.post('/couriers/accounts/$provider/test');
    return ConnectionTestResult.fromJson(json);
  }

  Future<CourierAccount> disconnect(String provider) async {
    final json = await api.delete('/couriers/accounts/$provider');
    return CourierAccount.fromJson(json);
  }

  Future<ProviderEvidence> evidence(String provider) async {
    final json = await api.get('/couriers/providers/$provider/evidence');
    return ProviderEvidence.fromJson(json);
  }

  // --- booking --------------------------------------------------------------

  Future<BookingReport> book(
    String orderId, {
    String provider = 'steadfast',
    String? note,
    String? itemDescription,
    int? deliveryType,
  }) async {
    final json = await api.post(
      '/couriers/orders/$orderId/book',
      query: <String, dynamic>{'provider': provider},
      body: <String, dynamic>{
        if (note != null && note.isNotEmpty) 'note': note,
        if (itemDescription != null && itemDescription.isNotEmpty)
          'item_description': itemDescription,
        if (deliveryType != null) 'delivery_type': deliveryType,
      },
    );
    return BookingReport.fromJson(json);
  }

  Future<BookingReport> bookBulk(
    List<String> orderIds, {
    String provider = 'steadfast',
    String? note,
  }) async {
    final json = await api.post(
      '/couriers/orders/book-bulk',
      query: <String, dynamic>{'provider': provider},
      body: <String, dynamic>{
        'order_ids': orderIds,
        if (note != null && note.isNotEmpty) 'note': note,
      },
    );
    return BookingReport.fromJson(json);
  }

  Future<ConsignmentTracking> tracking(String consignmentId) async {
    final json = await api.get(
      '/couriers/consignments/$consignmentId/tracking',
    );
    return ConsignmentTracking.fromJson(json);
  }

  // --- returns --------------------------------------------------------------

  Future<CourierReturnRequest> requestReturn(
    String consignmentId, {
    String? reason,
  }) async {
    final json = await api.post(
      '/couriers/consignments/$consignmentId/return',
      body: <String, dynamic>{
        if (reason != null && reason.isNotEmpty) 'reason': reason,
      },
    );
    return CourierReturnRequest.fromJson(json);
  }

  Future<List<CourierReturnRequest>> returns(String consignmentId) async {
    final rows = await api.getList(
      '/couriers/consignments/$consignmentId/returns',
    );
    return <CourierReturnRequest>[
      for (final row in rows)
        CourierReturnRequest.fromJson(row as Map<String, dynamic>),
    ];
  }

  // --- payments -------------------------------------------------------------

  Future<List<ProviderPayment>> payments({
    String provider = 'steadfast',
    int limit = 50,
  }) async {
    final rows = await api.getList(
      '/couriers/payments',
      query: <String, dynamic>{'provider': provider, 'limit': limit},
    );
    return <ProviderPayment>[
      for (final row in rows)
        ProviderPayment.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<Map<String, dynamic>> syncPayments({
    String provider = 'steadfast',
  }) async {
    return api.post(
      '/couriers/payments/sync',
      query: <String, dynamic>{'provider': provider},
    );
  }
}
