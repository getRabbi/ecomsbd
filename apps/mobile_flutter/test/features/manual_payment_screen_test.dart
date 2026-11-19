import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/data/billing/manual_payment.dart';
import 'package:ecomsbd/features/billing/manual_payment_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/manual_payment_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

const _options = '/billing/manual-payments/options';
const _list = '/billing/manual-payments';

Map<String, dynamic> _money(int paisa) => {
  'amount_paisa': paisa,
  'currency': 'BDT',
};

Map<String, dynamic> _plan(String code, String name, int monthly) => {
  'code': code,
  'name': name,
  'monthly_price': _money(monthly),
  'amounts': [
    for (final months in const [1, 3, 6, 12])
      {'months': months, 'amount': _money(monthly * months)},
  ],
};

Map<String, dynamic> _offered({bool available = true}) => {
  'available': available,
  'unavailable_reason': available ? null : 'CHANNEL',
  'accounts': available
      ? [
          {'method': 'BKASH', 'number': '01700000001', 'label': 'Personal'},
          {'method': 'NAGAD', 'number': '01700000002', 'label': ''},
        ]
      : [],
  'plans': available
      ? [_plan('starter', 'Starter', 19900), _plan('pro', 'Pro', 39900)]
      : [],
  'min_months': 1,
  'max_months': 12,
};

Map<String, dynamic> _row(String state, {String? reason}) => {
  'id': 'sub-$state',
  'plan': 'starter',
  'months': 3,
  'amount': _money(59700),
  'method': 'BKASH',
  'payer_number_masked': '01700****77',
  'trx_id': 'TX${state.substring(0, 3)}1234',
  'state': state,
  'reason': reason,
  'period_end': state == 'APPROVED' ? '2027-02-17T06:00:00+00:00' : null,
  'reviewed_at': null,
  'created_at': '2026-11-19T06:00:00+00:00',
};

Widget _app(_Fake fake, String language) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [apiClientProvider.overrideWithValue(fake.client)],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('en'), Locale('bn')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const ManualPaymentScreen(),
    ),
  );
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English payment string has Bangla', () {
    expect(manualPaymentBn.keys.toSet(), manualPaymentEn.keys.toSet());
    expect(manualPaymentBn.values.where((v) => v.trim().isEmpty), isEmpty);
    for (final state in const ['SUBMITTED', 'APPROVED', 'REJECTED']) {
      expect(manualPaymentEn.containsKey('mp.state.$state'), isTrue);
    }
  });

  test('the TrxID and payer number are checked the way the server checks', () {
    expect(cleanTrxId(' 8n7a 6d5c-4b '), '8N7A6D5C4B');
    expect(cleanTrxId('৮N৭A৬D৫C৪B'), '8N7A6D5C4B');
    expect(isTrxId('8N7A6D5C4B'), isTrue);
    expect(isTrxId('12345'), isFalse);
    expect(isTrxId('AB\$#12'), isFalse);
    expect(isBdMobile('০১৭০০০০০০৭৭'), isTrue);
    expect(isBdMobile('+8801700000077'), isTrue);
    expect(isBdMobile('0123456789'), isFalse);
    expect(
      manualPaymentRefusalKey({'code': 'TRX_ID_ALREADY_USED'}),
      'mp.errUsed',
    );
    expect(manualPaymentRefusalKey(null), isNull);
  });

  for (final language in ['en', 'bn']) {
    final strings = language == 'bn' ? manualPaymentBn : manualPaymentEn;

    testWidgets('choose, send, submit the TrxID in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', _options, _offered());
      fake.adapter.onJson('GET', _list, <Object>[]);
      fake.adapter.onJson('POST', _list, _row('SUBMITTED'));
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();

      // The store-build claim rides on every request (master spec §27.1).
      expect(
        fake.adapter
            .to('GET', _options)
            .single
            .headers['x-distribution-channel'],
        'PLAY',
      );
      expect(find.text(strings['mp.title']!), findsOneWidget);
      expect(find.text('01700000001 · Personal'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('mp.months.3')));
      await tester.pumpAndSettle();
      expect(
        tester.widget<Text>(find.byKey(const ValueKey('mp.amount'))).data,
        '৳597',
      );

      // Nothing is sent until both fields read right.
      await tester.ensureVisible(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();
      expect(find.text(strings['mp.invalidPayer']!), findsOneWidget);
      expect(find.text(strings['mp.invalidTrx']!), findsOneWidget);
      expect(fake.adapter.to('POST', _list), isEmpty);

      await tester.enterText(
        find.byKey(const ValueKey('mp.payer')),
        '01700000077',
      );
      await tester.enterText(
        find.byKey(const ValueKey('mp.trx')),
        '8n7a 6d5c-4b',
      );
      await tester.ensureVisible(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();

      final sent = fake.adapter.to('POST', _list).single.jsonBody;
      expect(sent, {
        'plan_code': 'starter',
        'months': 3,
        'method': 'BKASH',
        'payer_number': '01700000077',
        'trx_id': '8N7A6D5C4B',
      });
      expect(find.text(strings['mp.sent']!), findsOneWidget);
    });

    testWidgets('a used TrxID is refused in plain words in $language', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', _options, _offered());
      fake.adapter.onJson('GET', _list, <Object>[]);
      fake.adapter.on(
        'POST',
        _list,
        (_) => const FakeReply({
          'code': 'CONFLICT',
          'message_bn': 'এই TrxID আগেই জমা দেওয়া হয়েছে',
          'message_en': 'This TrxID has already been submitted',
          'retryable': false,
          'details': {'code': 'TRX_ID_ALREADY_USED', 'field': 'trx_id'},
        }, statusCode: 409),
      );
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('mp.payer')),
        '01700000077',
      );
      await tester.enterText(
        find.byKey(const ValueKey('mp.trx')),
        'TX12345678',
      );
      await tester.ensureVisible(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('mp.submit')));
      await tester.pumpAndSettle();
      expect(
        tester.widget<Text>(find.byKey(const ValueKey('mp.refusal'))).data,
        strings['mp.errUsed'],
      );
    });

    testWidgets('fits a 360x800 phone with its history in $language', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', _options, _offered());
      fake.adapter.onJson('GET', _list, [
        _row('SUBMITTED'),
        _row('APPROVED'),
        _row(
          'REJECTED',
          reason: 'No payment with this TrxID reached our bKash account',
        ),
      ]);
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.scrollUntilVisible(
        find.text(strings['mp.state.REJECTED']!),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      expect(find.text(strings['mp.state.APPROVED']!), findsOneWidget);
      expect(find.textContaining('No payment with this TrxID'), findsOneWidget);
    });

    testWidgets('says so when not offered in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', _options, _offered(available: false));
      await tester.pumpWidget(_app(fake, language));
      await tester.pumpAndSettle();
      expect(find.text(strings['mp.errUnavailable']!), findsOneWidget);
      expect(find.byKey(const ValueKey('mp.submit')), findsNothing);
      expect(fake.adapter.to('GET', _list), isEmpty);
    });
  }
}
