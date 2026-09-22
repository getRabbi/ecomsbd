import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/messaging/messaging_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

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
}
