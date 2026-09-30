import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/data/commerce/commerce_providers.dart';
import 'package:ecomsbd/data/local/database.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

export '../data/fake_api.dart';

/// A signed-in shop with a fake server, for the commerce screens.
///
/// The screens under test run against the real repositories, the real
/// controllers and the real HTTP client — only the socket and the database file
/// are swapped. That is deliberate: a screen test that stubs the repository
/// proves the widget renders a list, not that the app talks to the API it
/// actually ships against.
class CommerceHarness {
  CommerceHarness({this.extraOverrides = const <Override>[]})
    : db = EcomsbdDatabase.memory(),
      _fake = buildFakeApi() {
    adapter = _fake.adapter;
  }

  /// Screen-specific fakes, such as a platform sign-in sheet.
  final List<Override> extraOverrides;

  final EcomsbdDatabase db;
  final ({dynamic client, FakeApiAdapter adapter}) _fake;
  late final FakeApiAdapter adapter;

  bool get offline => adapter.offline;
  set offline(bool value) => adapter.offline = value;

  List<Override> get overrides => <Override>[
    databaseProvider.overrideWithValue(db),
    apiClientProvider.overrideWithValue(_fake.client),
    tenantIdProvider.overrideWithValue(testTenantId),
    // Home shows the shop name, which the real provider reads from the auth
    // controller, and that needs a live Supabase client.
    shopNameProvider.overrideWithValue('Test shop'),
    ...extraOverrides,
  ];

  Future<void> dispose() => db.close();
}

/// Render [screen] with the harness wired in, and let the first load settle.
Future<CommerceHarness> pumpCommerceScreen(
  WidgetTester tester,
  Widget screen, {
  CommerceHarness? harness,
  Size size = referencePhone,
}) async {
  final active = harness ?? CommerceHarness();
  addTearDown(() async {
    // Let anything still in flight land before the tree goes away. A screen
    // that fires two or three reads on open leaves their continuations pending
    // otherwise, and the binding reports them as leaked timers.
    await settle(tester, frames: 3);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    await active.dispose();
  });

  await pumpAtSize(
    tester,
    Scaffold(body: screen),
    size: size,
    overrides: active.overrides,
  );
  // The list controllers fetch in their constructor, and a detail screen fires
  // two or three reads at once; these frames let them all resolve and paint.
  await settle(tester, frames: 6, step: const Duration(milliseconds: 50));
  return active;
}

/// Pump a fixed number of frames.
///
/// `pumpAndSettle` is unusable on these screens: [SkeletonLoader] runs a
/// repeating animation, so the tree is never quiet and the call would sit until
/// its ten-minute timeout. A bounded pump is both faster and honest about what
/// it is waiting for.
Future<void> settle(
  WidgetTester tester, {
  int frames = 8,
  Duration step = const Duration(milliseconds: 60),
}) async {
  for (var i = 0; i < frames; i++) {
    await tester.pump(step);
  }
}

/// Fails if the widget tree overflowed.
///
/// Master spec section 53 targets a 360dp Android device, and an overflow there
/// means a seller cannot read a number they need.
void expectNoOverflow(WidgetTester tester) {
  expect(
    tester.takeException(),
    isNull,
    reason: 'the layout overflowed at this size',
  );
}

// --------------------------------------------------------------------------- //
// Fixtures
//
// Shaped exactly like the API responses in `backend/app/api/v1`, so a contract
// change surfaces here rather than on a device.
// --------------------------------------------------------------------------- //

Map<String, dynamic> productJson({
  String id = 'p1',
  String name = 'Cotton Abaya',
  int stock = 12,
  int cost = 40000,
  int price = 125000,
  int? threshold = 3,
  bool archived = false,
}) => <String, dynamic>{
  'id': id,
  'name': name,
  'sku': 'SKU-$id',
  'description': null,
  'cost_paisa': cost,
  'default_selling_price_paisa': price,
  'stock_tracking_enabled': true,
  'stock_on_hand': stock,
  'low_stock_threshold': threshold,
  'is_low_stock': threshold != null && stock <= threshold,
  'is_active': true,
  'is_archived': archived,
  'created_at': '2026-09-01T10:00:00Z',
  'updated_at': '2026-09-09T10:00:00Z',
};

Map<String, dynamic> customerJson({
  String id = 'c1',
  String? name = 'Nusrat Jahan',
  int orders = 3,
  int delivered = 2,
  int returned = 1,
  int? successBasisPoints = 6667,
  String flag = 'NONE',
  bool repeat = true,
}) => <String, dynamic>{
  'id': id,
  'name': name,
  'phone_masked': '01712****78',
  'phone_last4': '5678',
  'flag': flag,
  'is_repeat_buyer': repeat,
  'order_count': orders,
  'delivered_count': delivered,
  'returned_count': returned,
  'cancelled_count': 0,
  'success_rate_basis_points': successBasisPoints,
  'realized_revenue_paisa': 284500,
  'first_order_at': '2026-08-01T10:00:00Z',
  'last_order_at': '2026-09-08T10:00:00Z',
  'created_at': '2026-08-01T10:00:00Z',
  'notes': null,
  'flag_reason': null,
  'addresses': <dynamic>[],
};

Map<String, dynamic> orderJson({
  String id = 'o1',
  String number = 'CP-20260910-0042',
  String status = 'CONFIRMED',
  int cod = 125000,
  int version = 1,
  String fulfillment = 'NOT_BOOKED',
  String risk = 'NOT_CHECKED',
  String profit = 'PENDING_CALCULATION',
}) => <String, dynamic>{
  'id': id,
  'order_number': number,
  'client_id': 'client-$id',
  'customer_id': 'c1',
  'customer_name': 'Nusrat Jahan',
  'customer_phone_masked': '01712****78',
  'delivery_address_raw': 'House 4, Road 2, Dhanmondi',
  'delivery_district': 'Dhaka',
  'delivery_area': 'Dhanmondi',
  'status': status,
  'channel': 'MANUAL',
  'business_date': '2026-09-10',
  'subtotal_paisa': cod,
  'discount_paisa': 0,
  'delivery_fee_paisa': 0,
  'cod_amount_paisa': cod,
  'note': null,
  'version': version,
  'created_at': '2026-09-10T04:00:00Z',
  'updated_at': '2026-09-10T04:00:00Z',
  'fulfillment_state': fulfillment,
  'risk_state': risk,
  'profit_state': profit,
  'items': <dynamic>[
    <String, dynamic>{
      'id': 'i1',
      'product_id': 'p1',
      'product_name': 'Cotton Abaya',
      'sku': 'SKU-p1',
      'variant_label': 'XL',
      'quantity': 1,
      'unit_price_paisa': cod,
      'unit_cost_snapshot_paisa': 40000,
      'discount_paisa': 0,
      'line_total_paisa': cod,
      'note': null,
    },
  ],
  'source_text': null,
  'estimated_item_cost_paisa': 40000,
};

Map<String, dynamic> page(List<Map<String, dynamic>> items) =>
    <String, dynamic>{'items': items, 'next_cursor': null, 'has_more': false};

Map<String, dynamic> moneySummaryJson({
  int outstanding = 480_500,
  int settled = 140_500,
  int unpaidCount = 3,
  int unknownDeduction = 0,
  int openCases = 0,
  List<Map<String, dynamic>>? aging,
}) => <String, dynamic>{
  'outstanding_paisa': outstanding,
  'settled_paisa': settled,
  'unpaid_parcel_count': unpaidCount,
  'courier_charge_paisa': 24_000,
  'cod_fee_paisa': 8_000,
  'return_charge_paisa': 0,
  'unknown_deduction_paisa': unknownDeduction,
  'write_off_paisa': 0,
  'unexplained_payout_paisa': 0,
  'open_case_count': openCases,
  'aging':
      aging ??
      <Map<String, dynamic>>[
        agingBandJson('0-3 days', 0, 3, 3, outstanding),
        agingBandJson('4-7 days', 4, 7, 0, 0),
        agingBandJson('8-14 days', 8, 14, 0, 0),
        agingBandJson('15+ days', 15, null, 0, 0),
      ],
  'since': null,
  'until': null,
};

Map<String, dynamic> agingBandJson(
  String label,
  int minDays,
  int? maxDays,
  int count,
  int outstanding,
) => <String, dynamic>{
  'label': label,
  'min_days': minDays,
  'max_days': maxDays,
  'parcel_count': count,
  'outstanding_paisa': outstanding,
};

/// Drag the outermost scrollable until [finder] appears.
///
/// `scrollUntilVisible` needs the item to exist in the tree already, which is
/// not true of a lazily built list, and a single fixed drag is a guess that
/// breaks the moment a card grows a line. This walks instead.
Future<void> scrollTo(
  WidgetTester tester,
  Finder finder, {
  double step = -400,
  int maxDrags = 12,
}) async {
  for (var i = 0; i < maxDrags; i++) {
    if (finder.evaluate().isNotEmpty) return;
    final scrollable = find.byType(Scrollable).first;
    await tester.drag(scrollable, Offset(0, step));
    await settle(tester, frames: 3);
  }
}
