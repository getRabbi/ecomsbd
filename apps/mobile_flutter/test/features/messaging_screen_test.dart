import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/messaging/messaging_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

Widget _app(ApiClient client, String language) => ProviderScope(
  overrides: [apiClientProvider.overrideWithValue(client)],
  child: MaterialApp(
    locale: Locale(language),
    supportedLocales: const [Locale('en'), Locale('bn')],
    localizationsDelegates: const [
      AppStrings.delegate,
      GlobalMaterialLocalizations.delegate,
      GlobalWidgetsLocalizations.delegate,
      GlobalCupertinoLocalizations.delegate,
    ],
    home: const MessagingScreen(),
  ),
);

void main() {
  for (final language in ['en', 'bn']) {
    testWidgets('unavailable channels cannot be enabled ($language)', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/messaging/channels', {
        'items': [
          {'kind': 'WHATSAPP', 'available': false, 'enabled': false},
        ],
      });
      for (final path in [
        '/messaging/conversations',
        '/customers',
        '/messaging/templates',
      ]) {
        fake.adapter.onJson('GET', path, {'items': []});
      }
      await tester.pumpWidget(
        ProviderScope(
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
            home: const MessagingScreen(),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('WHATSAPP'), findsOneWidget);
      expect(
        tester.widget<SwitchListTile>(find.byType(SwitchListTile)).onChanged,
        isNull,
      );
      expect(fake.adapter.requests.where((r) => r.method == 'POST'), isEmpty);
      expect(
        find.text(
          language == 'en'
              ? 'Official provider required'
              : 'অফিশিয়াল প্রোভাইডার প্রয়োজন',
        ),
        findsOneWidget,
      );
    });
  }

  for (final language in ['en', 'bn']) {
    testWidgets('an SMS contact uses the saved phone at 360x800 ($language)', (
      tester,
    ) async {
      final en = language == 'en';
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/messaging/channels', {
        'items': [
          {'kind': 'EMAIL', 'available': false, 'enabled': false},
          {'kind': 'SMS', 'available': true, 'enabled': true},
        ],
      });
      fake.adapter.onJson('GET', '/customers', {
        'items': [
          {'id': 'c1', 'name': 'Rahim', 'phone_masked': '01700****71'},
        ],
      });
      for (final path in [
        '/messaging/conversations',
        '/messaging/templates',
        '/orders',
        '/messaging/messages',
      ]) {
        fake.adapter.onJson('GET', path, {'items': []});
      }
      fake.adapter.onJson('POST', '/messaging/conversations', {
        'id': 'v1',
        'customer_id': 'c1',
        'channel': 'SMS',
        'recipient_masked': '01700****71',
        'consent': true,
        'marketing_consent': false,
      });
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(fake.client, language));
      await tester.pumpAndSettle();

      expect(
        find.text(en ? 'Order updates only' : 'শুধু অর্ডারের আপডেট'),
        findsOneWidget,
      );
      await tester.tap(
        find.text(
          en ? 'Record contact and consent' : 'যোগাযোগ ও সম্মতি সংরক্ষণ',
        ),
      );
      await tester.pumpAndSettle();
      expect(find.byType(CheckboxListTile), findsOneWidget); // consent only
      await tester.tap(find.byType(DropdownButtonFormField<String>).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Rahim').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('contact-channel')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('SMS').last);
      await tester.pumpAndSettle();
      expect(
        find.text(
          en
              ? 'Use the customer’s saved phone number'
              : 'গ্রাহকের সংরক্ষিত ফোন নম্বর ব্যবহার করুন',
        ),
        findsOneWidget,
      );
      expect(find.text(en ? 'Mobile number' : 'মোবাইল নম্বর'), findsNothing);
      final save = find.text(en ? 'Save' : 'সংরক্ষণ');
      await tester.ensureVisible(save);
      await tester.pumpAndSettle();
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);

      final sent = fake.adapter.to('POST', '/messaging/conversations').single;
      expect(sent.jsonBody['channel'], 'SMS');
      expect(sent.jsonBody['use_customer_phone'], isTrue);
      expect(sent.jsonBody.containsKey('recipient'), isFalse);
    });
  }

  testWidgets('without the SMS gateway the contact form stays email only', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.onJson('GET', '/messaging/channels', {
      'items': [
        {'kind': 'SMS', 'available': false, 'enabled': false},
      ],
    });
    for (final path in [
      '/messaging/conversations',
      '/customers',
      '/messaging/templates',
    ]) {
      fake.adapter.onJson('GET', path, {'items': []});
    }
    await tester.pumpWidget(_app(fake.client, 'en'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Record contact and consent'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('contact-channel')), findsNothing);
    expect(find.text('Email'), findsOneWidget);
  });
}
