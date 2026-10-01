import 'package:ecomsbd/features/inbox/inbox_screen.dart';
import 'package:ecomsbd/features/orders/order_compose_screen.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

/// The Inbox as order automation: drafts read from Messenger and WhatsApp
/// messages, reviewed in the normal order form, confirmed once.

const Size _tall = Size(360, 2400);

String _en(String key) => englishStrings[key]!;

Map<String, dynamic> _draft({
  String id = 'd1',
  String provider = 'MESSENGER',
  String status = 'READY_FOR_REVIEW',
  List<Map<String, dynamic>>? items,
  List<String> missing = const <String>[],
  List<String> warnings = const <String>[],
  String? phone = '+8801712345678',
  String? address = 'মিরপুর 10 ঢাকা',
}) => <String, dynamic>{
  'id': id,
  'provider': provider,
  'status': status,
  'connection_id': 'conn1',
  'channel_name': 'Rahim Fashion',
  'sender': <String, dynamic>{
    'display_name': null,
    'phone_masked': null,
    'known': false,
  },
  'customer_match': 'NONE',
  'customer': null,
  'customer_name': 'রহিম',
  'phones': <String>[if (phone != null) phone],
  'selected_phone': phone,
  'address': address,
  'items':
      items ??
      <Map<String, dynamic>>[
        <String, dynamic>{
          'name': 'কালো পাঞ্জাবি',
          'quantity': 2,
          'size': 'XL',
          'color': 'কালো',
          'match': <String, dynamic>{
            'status': 'MATCHED',
            'product_id': 'p1',
            'product_name': 'Black Panjabi',
            'variant_status': 'MATCHED',
            'variant_id': 'v1',
            'variant_name': 'Black / XL',
            'unit_price_paisa': 92500,
          },
        },
      ],
  'cod_amount_paisa': 185000,
  'cod_source': 'CATALOG',
  'notes': null,
  'warnings': warnings,
  'uncertain_fields': <String>['amount'],
  'missing_fields': missing,
  'message_count': 4,
  'attachment_count': 0,
  'first_message_at': '2026-10-01T05:00:00Z',
  'last_message_at': '2026-10-01T05:02:00Z',
  'ready_at': '2026-10-01T05:02:00Z',
  'closed_at': null,
  'confirmed_order_id': null,
  'created_at': '2026-10-01T05:00:00Z',
  'updated_at': '2026-10-01T05:02:00Z',
};

CommerceHarness _inbox({
  List<Map<String, dynamic>>? drafts,
  bool connected = true,
}) {
  final harness = CommerceHarness();
  harness.adapter
    ..onJson('GET', '/integrations', <String, dynamic>{
      'providers': <dynamic>[],
      'items': <dynamic>[],
      'can_manage': true,
      'can_retry': true,
    })
    ..onJson('GET', '/chat-orders/summary', <String, dynamic>{
      'ready': 1,
      'needs_info': 0,
      'attention': 0,
      'channels': <String, dynamic>{'MESSENGER': connected, 'WHATSAPP': false},
    })
    ..onJson('GET', '/chat-orders', <String, dynamic>{
      'items': drafts ?? <Map<String, dynamic>>[_draft()],
      'next_offset': 1,
    })
    ..onJson('GET', '/chat-orders/attention', <String, dynamic>{
      'items': <dynamic>[],
      'next_offset': 0,
    })
    ..onJson('POST', '/orders/check-duplicates', <String, dynamic>{
      'possible_duplicate': false,
      'message': null,
      'window_hours': 24,
      'candidates': <dynamic>[],
    })
    ..onJson('GET', '/customers/lookup', <String, dynamic>{})
    ..onJson('GET', '/orders', page(<Map<String, dynamic>>[]));
  return harness;
}

Map<String, dynamic> _confirmed() => <String, dynamic>{
  'order': orderJson(id: 'o9', number: 'CP-20261001-0009', status: 'DRAFT'),
  'duplicate_check': null,
  'replayed': false,
  'draft': _draft(status: 'CONFIRMED'),
};

void main() {
  testWidgets(
    'a ready chat draft shows its order, missing and uncertain bits',
    (tester) async {
      final harness = _inbox(
        drafts: <Map<String, dynamic>>[
          _draft(),
          _draft(
            id: 'd2',
            provider: 'WHATSAPP',
            status: 'NEEDS_INFO',
            missing: <String>['address'],
            address: null,
          ),
        ],
      );
      await pumpCommerceScreen(
        tester,
        const InboxScreen(),
        harness: harness,
        size: _tall,
      );
      expect(find.text('New order from Messenger'), findsOneWidget);
      expect(find.text('New order from WhatsApp'), findsOneWidget);
      expect(find.text('Black Panjabi / Black / XL'), findsNWidgets(2));
      expect(find.text('+8801712345678'), findsNWidgets(2));
      expect(find.text('Missing: address'), findsOneWidget);
      expect(find.textContaining('(from your prices)'), findsNWidgets(2));
      // The old "conversations are not synced" dead end is gone; the manual
      // paste fallback stays.
      expect(find.text(_en('inbox.detect')), findsOneWidget);
      expect(
        harness.adapter.to('GET', '/chat-orders').first.query['status'],
        'review',
      );

      // The WhatsApp filter shows WhatsApp drafts only.
      await tester.tap(find.text(_en('chan.whatsapp')).first);
      await settle(tester);
      expect(find.text('New order from Messenger'), findsNothing);
      expect(find.text('New order from WhatsApp'), findsOneWidget);
      expectNoOverflow(tester);
    },
  );

  testWidgets('ignore closes the draft on the server', (tester) async {
    final harness = _inbox()
      ..adapter.onJson(
        'POST',
        '/chat-orders/d1/ignore',
        _draft(status: 'IGNORED'),
      );
    await pumpCommerceScreen(
      tester,
      const InboxScreen(),
      harness: harness,
      size: _tall,
    );
    await tester.tap(find.byKey(const ValueKey('chat-ignore-d1')));
    await settle(tester);
    expect(harness.adapter.to('POST', '/chat-orders/d1/ignore'), hasLength(1));
    expect(find.text(_en('cho.ignored')), findsOneWidget);
  });

  testWidgets('review opens the order form prefilled; confirm creates once', (
    tester,
  ) async {
    final harness = _inbox()
      ..adapter.onJson('POST', '/chat-orders/d1/confirm', _confirmed());
    await pumpCommerceScreen(
      tester,
      const InboxScreen(),
      harness: harness,
      size: _tall,
    );
    await tester.tap(find.byKey(const ValueKey('chat-review-d1')));
    await settle(tester, frames: 12);
    expect(find.byType(OrderComposeScreen), findsOneWidget);
    expect(find.text('Order from Messenger'), findsOneWidget);
    expect(find.widgetWithText(TextField, '+8801712345678'), findsOneWidget);
    expect(find.widgetWithText(TextField, 'মিরপুর 10 ঢাকা'), findsOneWidget);
    expect(find.text('Black Panjabi / Black / XL'), findsOneWidget);

    final confirm = find.text(_en('cho.confirmOrder'));
    await tester.ensureVisible(confirm);
    await tester.tap(confirm);
    await settle(tester, frames: 12);

    final sent = harness.adapter.to('POST', '/chat-orders/d1/confirm');
    expect(sent, hasLength(1));
    final body = sent.single.jsonBody;
    expect(body['phone'], '+8801712345678');
    expect(body['customer_name'], 'রহিম');
    final item = (body['items'] as List<dynamic>).single as Map;
    expect(item['product_id'], 'p1');
    expect(item['variant_id'], 'v1');
    expect(item['quantity'], 2);
    // Never the normal create: one order per draft, made by the server.
    expect(harness.adapter.to('POST', '/orders'), isEmpty);
    expect(find.byType(OrderComposeScreen), findsNothing);
    expect(find.textContaining('CP-20261001-0009'), findsOneWidget);
  });

  testWidgets('an ambiguous product must be chosen before confirming', (
    tester,
  ) async {
    final harness = _inbox(
      drafts: <Map<String, dynamic>>[
        _draft(
          warnings: <String>['PRODUCT_AMBIGUOUS'],
          items: <Map<String, dynamic>>[
            <String, dynamic>{
              'name': 'panjabi',
              'quantity': 2,
              'size': null,
              'color': null,
              'match': <String, dynamic>{
                'status': 'AMBIGUOUS',
                'candidates': <dynamic>[
                  <String, dynamic>{
                    'product_id': 'p1',
                    'name': 'Cotton Panjabi',
                  },
                  <String, dynamic>{'product_id': 'p2', 'name': 'Silk Panjabi'},
                ],
              },
            },
          ],
        ),
      ],
    );
    harness.adapter
      ..onJson(
        'GET',
        '/products/p2',
        productJson(id: 'p2', name: 'Silk Panjabi'),
      )
      ..onJson('POST', '/chat-orders/d1/confirm', _confirmed());
    await pumpCommerceScreen(
      tester,
      const InboxScreen(),
      harness: harness,
      size: _tall,
    );
    expect(find.text(_en('cho.warn.PRODUCT_AMBIGUOUS')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('chat-review-d1')));
    await settle(tester, frames: 12);
    expect(find.text(_en('cho.productAmbiguous')), findsOneWidget);

    final confirm = find.text(_en('cho.confirmOrder'));
    await tester.ensureVisible(confirm);
    await tester.tap(confirm);
    await settle(tester, frames: 6);
    expect(harness.adapter.to('POST', '/chat-orders/d1/confirm'), isEmpty);
    expect(find.text('Choose which product the customer meant.'), findsOne);

    final choose = find.text(_en('cho.choose'));
    await tester.ensureVisible(choose);
    await tester.tap(choose);
    await settle(tester, frames: 8);
    await tester.tap(find.byKey(const ValueKey('pick-suggestion-p2')));
    await settle(tester, frames: 8);
    expect(find.text('Silk Panjabi'), findsWidgets);

    await tester.ensureVisible(confirm);
    await tester.tap(confirm);
    await settle(tester, frames: 12);
    final sent = harness.adapter.to('POST', '/chat-orders/d1/confirm');
    expect(sent, hasLength(1));
    final item = (sent.single.jsonBody['items'] as List<dynamic>).single as Map;
    expect(item['product_id'], 'p2');
  });

  testWidgets('with no chat channel connected the Inbox says how to start', (
    tester,
  ) async {
    await pumpCommerceScreen(
      tester,
      const InboxScreen(),
      harness: _inbox(drafts: <Map<String, dynamic>>[], connected: false),
      size: _tall,
    );
    expect(find.text(_en('cho.connectFirst')), findsOneWidget);
    expect(find.text(_en('cho.emptyTitle')), findsOneWidget);
  });

  testWidgets('pasting a message still detects an order and saves it', (
    tester,
  ) async {
    final harness = _inbox(drafts: <Map<String, dynamic>>[])
      ..adapter.onJson('POST', '/orders/parse', <String, dynamic>{
        'customer_name': 'Nusrat',
        'phones': <String>['+8801712345678'],
        'selected_phone': '+8801712345678',
        'address': 'Mirpur 10, Dhaka',
        'items': <dynamic>[
          <String, dynamic>{
            'name': 'Black Abaya',
            'quantity': 1,
            'size': 'XL',
            'color': 'Black',
            'unit_price_paisa': null,
          },
        ],
        'cod_amount_paisa': 125000,
        'notes': null,
        'confidence': <String, dynamic>{
          'phone': 1.0,
          'address': 0.8,
          'items': 0.5,
          'amount': 0.8,
          'name': 0.5,
        },
        'parser': 'deterministic',
        'warnings': <String>[],
        'needs_phone_selection': false,
        'is_low_confidence': false,
        'source_text': 'Nusrat\n01712345678',
      })
      ..adapter.onJson('POST', '/orders', <String, dynamic>{
        'order': orderJson(id: 'o5', number: 'CP-1'),
        'duplicate_check': null,
      });
    await pumpCommerceScreen(
      tester,
      const InboxScreen(),
      harness: harness,
      size: _tall,
    );
    await tester.enterText(
      find.widgetWithText(TextField, _en('inbox.detectHint')),
      'Nusrat\n01712345678',
    );
    await tester.tap(find.text(_en('inbox.detect')));
    await settle(tester, frames: 10);
    expect(find.text(_en('inbox.detected')), findsOneWidget);
    // "Black" is already in the product name; it is not said twice.
    expect(find.text('Black Abaya XL'), findsOneWidget);
    await tester.tap(find.text(_en('inbox.createOrder')));
    await settle(tester, frames: 12);
    final save = find.text(_en('oc.saveOrder'));
    await tester.ensureVisible(save);
    await tester.tap(save);
    await settle(tester, frames: 12);
    final created = harness.adapter.to('POST', '/orders');
    expect(created, hasLength(1));
    expect(created.single.jsonBody['channel'], 'PASTE_PARSE');
  });
}
