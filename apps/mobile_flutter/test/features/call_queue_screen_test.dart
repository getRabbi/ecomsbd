import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/orders/call_queue_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/call_queue_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

const _orderId = '00000000-0000-0000-0000-0000000000a1';

Map<String, dynamic> _item({
  String id = _orderId,
  String reason = 'VERIFY_NEW',
  Map<String, dynamic>? lastCall,
  int calls = 0,
}) => {
  'confirmation': {
    'id': 'c-$id',
    'order_id': id,
    'state': 'CALL_QUEUED',
    'reason_code': reason,
    'attempt_count': calls,
  },
  'order': {
    'id': id,
    'order_number': 'ORD-1042',
    'status': 'DRAFT',
    'customer_name': 'Nusrat Jahan Chowdhury Rupa',
    'customer_phone_masked': '01700****51',
    'cod_amount_paisa': 125000,
    'created_at': '2026-11-19T06:00:00+00:00',
  },
  'last_call': lastCall,
  'manual_calls': calls,
};

Map<String, dynamic> _queue(
  List<Map<String, dynamic>> items, {
  bool quiet = false,
}) => {
  'voice_available': false,
  'quiet_hours': {
    'start': '21:00',
    'end': '09:00',
    'active': quiet,
    'ends_at': null,
  },
  'items': items,
};

Widget _app(_Fake fake, String language, {List<String>? dialed}) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [
      apiClientProvider.overrideWithValue(fake.client),
      dialerProvider.overrideWithValue((phone) async {
        dialed?.add(phone);
        return true;
      }),
    ],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('en'), Locale('bn')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const CallQueueScreen(),
    ),
  );
}

/// The phone app takes over, then the seller comes back.
Future<void> _leaveAndComeBack(WidgetTester tester) async {
  for (final state in const [
    AppLifecycleState.inactive,
    AppLifecycleState.hidden,
    AppLifecycleState.paused,
    AppLifecycleState.hidden,
    AppLifecycleState.inactive,
    AppLifecycleState.resumed,
  ]) {
    tester.binding.handleAppLifecycleStateChanged(state);
    await tester.pump();
  }
  await tester.pumpAndSettle();
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English call-queue string has Bangla', () {
    expect(callQueueBn.keys.toSet(), callQueueEn.keys.toSet());
    expect(callQueueBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  test('each of the policy reason codes has a label in both languages', () {
    expect(callQueueReasonCodes, hasLength(8));
    for (final code in callQueueReasonCodes) {
      expect(callReasonKey(code), 'cq.reason.$code');
      expect(callQueueEn.containsKey('cq.reason.$code'), isTrue, reason: code);
      expect(callQueueBn.containsKey('cq.reason.$code'), isTrue, reason: code);
    }
    expect(callReasonKey('SOMETHING_NEW'), 'cq.reason.other');
    expect(callReasonKey(null), 'cq.reason.other');
  });

  for (final language in ['en', 'bn']) {
    final strings = language == 'bn' ? callQueueBn : callQueueEn;

    testWidgets('call, come back, confirm in $language', (tester) async {
      final fake = buildFakeApi();
      final dialed = <String>[];
      fake.adapter.onJson('GET', '/confirmations/queue', _queue([_item()]));
      fake.adapter.onJson('POST', '/orders/$_orderId/confirmation/call_now', {
        'phone': '+8801700000051',
        'quiet_override': false,
      });
      fake.adapter.onJson(
        'POST',
        '/orders/$_orderId/confirmation/mark_confirmed',
        {'confirmation': {}},
      );
      await tester.pumpWidget(_app(fake, language, dialed: dialed));
      await tester.pumpAndSettle();

      expect(find.text(strings['cq.reason.VERIFY_NEW']!), findsOneWidget);
      expect(find.textContaining('৳1,250'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('cq.call.$_orderId')));
      await tester.pumpAndSettle();
      expect(dialed, ['+8801700000051']);
      expect(find.text(strings['cq.outcomeTitle']!), findsNothing);

      await _leaveAndComeBack(tester);
      expect(find.text(strings['cq.outcomeTitle']!), findsOneWidget);
      expect(find.text(strings['cq.noAnswer']!), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('cq.outcome.confirmed')));
      await tester.pumpAndSettle();

      expect(
        fake.adapter.to(
          'POST',
          '/orders/$_orderId/confirmation/mark_confirmed',
        ),
        hasLength(1),
      );
      expect(find.text(strings['cq.confirmed']!), findsOneWidget);
    });

    testWidgets('fits a 360x800 phone in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson(
        'GET',
        '/confirmations/queue',
        _queue(quiet: true, [
          _item(
            reason: 'CUSTOMER_BLOCKED',
            calls: 2,
            lastCall: {'attempt_no': 2, 'status': 'NO_ANSWER'},
          ),
          _item(id: '00000000-0000-0000-0000-0000000000a2'),
        ]),
      );
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text(strings['cq.reason.CUSTOMER_BLOCKED']!), findsOneWidget);
      expect(find.textContaining(strings['cq.lastNoAnswer']!), findsOneWidget);
    });
  }

  testWidgets('quiet hours ask before calling, and Dhoreni is saved', (
    tester,
  ) async {
    final fake = buildFakeApi();
    final dialed = <String>[];
    fake.adapter.onJson('GET', '/confirmations/queue', _queue([_item()]));
    fake.adapter.on('POST', '/orders/$_orderId/confirmation/call_now', (
      request,
    ) {
      if ((request.body as Map?)?['override_quiet'] == true) {
        return const FakeReply({
          'phone': '+8801700000051',
          'quiet_override': true,
        });
      }
      return const FakeReply({
        'code': 'CONFLICT',
        'message_bn': 'এখন কল না করার সময়',
        'message_en': 'It is the shop\'s quiet hours',
        'retryable': false,
        'details': {
          'code': 'CONFIRMATION_QUIET_HOURS',
          'quiet_start': '21:00',
          'quiet_end': '09:00',
        },
      }, statusCode: 409);
    });
    fake.adapter.onJson(
      'POST',
      '/orders/$_orderId/confirmation/mark_no_answer',
      {'confirmation': {}},
    );
    await tester.pumpWidget(_app(fake, 'en', dialed: dialed));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('cq.call.$_orderId')));
    await tester.pumpAndSettle();
    expect(find.text(callQueueEn['cq.quietTitle']!), findsOneWidget);
    expect(dialed, isEmpty);
    await tester.tap(find.byKey(const ValueKey('cq.quietOverride')));
    await tester.pumpAndSettle();
    expect(dialed, ['+8801700000051']);

    await _leaveAndComeBack(tester);
    await tester.tap(find.byKey(const ValueKey('cq.outcome.noAnswer')));
    await tester.pumpAndSettle();
    expect(
      fake.adapter.to('POST', '/orders/$_orderId/confirmation/mark_no_answer'),
      hasLength(1),
    );
    expect(find.text(callQueueEn['cq.noAnswerSaved']!), findsOneWidget);
  });

  testWidgets('a shop without the feature is told so', (tester) async {
    final fake = buildFakeApi();
    fake.adapter.on(
      'GET',
      '/confirmations/queue',
      (_) => const FakeReply({
        'code': 'FEATURE_DISABLED',
        'message_bn': 'চালু নেই',
        'message_en': 'Not enabled',
        'retryable': false,
      }, statusCode: 403),
    );
    await tester.pumpWidget(_app(fake, 'bn'));
    await tester.pumpAndSettle();
    expect(find.text(callQueueBn['cq.notEnabled']!), findsOneWidget);
  });

  testWidgets('the orders banner shows only when calls are waiting', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/confirmations/queue', _queue([_item()]));
    activeAppLocale = AppLocale.en;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiClientProvider.overrideWithValue(fake.client)],
        child: const MaterialApp(
          localizationsDelegates: [
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
          ],
          home: Scaffold(body: CallQueueBanner()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('1 orders waiting for a call'), findsOneWidget);

    final empty = buildFakeApi(); // unknown route → 404: nothing shown
    await tester.pumpWidget(
      ProviderScope(
        key: UniqueKey(),
        overrides: [apiClientProvider.overrideWithValue(empty.client)],
        child: const MaterialApp(
          localizationsDelegates: [
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
          ],
          home: Scaffold(body: CallQueueBanner()),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('cq.banner')), findsNothing);
  });

  test('a Dhoreni that sent the SMS link says so (EN + BN)', () {
    const sent = {
      'confirmation': {'state': 'SMS_LINK_SENT'},
    };
    const kept = {
      'confirmation': {'state': 'SELLER_REVIEW'},
    };
    expect(savedKey(CallOutcome.noAnswer, sent), 'cq.linkSent');
    expect(savedKey(CallOutcome.noAnswer, kept), 'cq.noAnswerSaved');
    expect(savedKey(CallOutcome.confirmed, sent), 'cq.confirmed');
    expect(callQueueEn['cq.linkSent'], isNotEmpty);
    expect(callQueueBn['cq.linkSent'], isNotEmpty);
  });
}
