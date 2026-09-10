import '../../core/api/api_client.dart';
import 'models.dart';

/// Billing, over the API.
///
/// Deliberately **not** a `CachingRepository`. Every other read in this app
/// falls back to the device cache when the network is gone, labelled with when
/// it was true — which is right for a stock count and wrong for an entitlement.
/// A cached "you are on Pro" is exactly the local `paid = true` that master
/// spec section 27.3 forbids, and it would be indistinguishable from a real
/// one. When billing cannot be read, the screen says so.
class BillingRepository {
  const BillingRepository(this._api);

  final ApiClient _api;

  // --- reading --------------------------------------------------------------

  Future<Entitlements> entitlements() async {
    return Entitlements.fromJson(await _api.get('/billing/entitlements'));
  }

  Future<List<Plan>> plans() async {
    final rows = await _api.getList('/billing/plans');
    return <Plan>[
      for (final row in rows) Plan.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<List<UsageLine>> usage() async {
    final json = await _api.get('/billing/usage');
    return <UsageLine>[
      for (final row in (json['usage'] as List<dynamic>? ?? const <dynamic>[]))
        UsageLine.fromJson(row as Map<String, dynamic>),
    ];
  }

  Future<BillingChannel> channel() async {
    return BillingChannel.fromJson(await _api.get('/billing/channel'));
  }

  Future<SubscriptionState?> subscription() async {
    final json = await _api.get('/billing/subscription');
    // A shop that has never touched billing has no subscription row. The
    // endpoint answers `null`, which is a real answer and not an error.
    if (json.isEmpty) return null;
    return SubscriptionState.fromJson(json);
  }

  Future<List<BillingEvent>> history({int limit = 25}) async {
    final rows = await _api.getList(
      '/billing/history',
      query: <String, dynamic>{'limit': limit},
    );
    return <BillingEvent>[
      for (final row in rows)
        BillingEvent.fromJson(row as Map<String, dynamic>),
    ];
  }

  /// Everything the plans screen needs, in one place.
  ///
  /// Fetched sequentially rather than in parallel: this runs on a phone on a
  /// Bangladeshi mobile network, and four simultaneous requests on a bad
  /// connection are slower and more likely to fail than four in a row.
  Future<BillingOverview> overview() async {
    final channelState = await channel();
    final planList = await plans();
    final current = await entitlements();
    final usageLines = await usage();
    final subscriptionState = await subscription();

    return BillingOverview(
      plans: planList,
      entitlements: current,
      usage: usageLines,
      channel: channelState,
      subscription: subscriptionState,
    );
  }

  // --- purchase -------------------------------------------------------------

  /// Hand a Play purchase token to the server for verification.
  ///
  /// The app never grants anything from this. It sends the token, and the
  /// server's answer — after it has asked Google — is what changes the plan.
  /// A `BILLING_VERIFICATION_FAILED` response is a real answer to show, not a
  /// failure to retry silently.
  Future<Map<String, dynamic>> verifyPlayPurchase({
    required String purchaseToken,
    required String productId,
    String? packageName,
  }) {
    return _api.post(
      '/billing/play/verify',
      body: <String, dynamic>{
        'purchase_token': purchaseToken,
        'product_id': productId,
        if (packageName != null) 'package_name': packageName,
      },
    );
  }

  /// Re-verify purchases the device still holds (master spec section 90).
  ///
  /// The path after a reinstall or a new phone. It runs the same verification
  /// as a fresh purchase, so it can never grant more than one would.
  Future<Map<String, dynamic>> restorePurchases(
    List<Map<String, String>> purchases,
  ) {
    return _api.post(
      '/billing/restore',
      body: <String, dynamic>{'purchases': purchases},
    );
  }

  Future<Map<String, dynamic>> startWebCheckout({
    required String plan,
    String? provider,
  }) {
    return _api.post(
      '/billing/web/checkout',
      body: <String, dynamic>{
        'plan': plan,
        if (provider != null) 'provider': provider,
      },
    );
  }

  Future<SubscriptionState> cancel({String? reason}) async {
    final json = await _api.post(
      '/billing/cancel',
      body: <String, dynamic>{
        'at_period_end': true,
        if (reason != null) 'reason': reason,
      },
    );
    return SubscriptionState.fromJson(json);
  }
}
