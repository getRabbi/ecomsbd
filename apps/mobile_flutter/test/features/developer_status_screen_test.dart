import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/features/settings/developer_status_screen.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import '../data/fake_api.dart';

Widget _app(dynamic client, Locale locale) {
  activeAppLocale = locale.languageCode == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [apiClientProvider.overrideWithValue(client)],
    child: MaterialApp(
      locale: locale,
      supportedLocales: const [Locale('bn'), Locale('en')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const DeveloperStatusScreen(),
    ),
  );
}

void main() {
  for (final locale in const [Locale('en'), Locale('bn')]) {
    testWidgets('shows API and webhook status and retries in $locale', (
      tester,
    ) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/developers/health', {
        'api': {
          'state': 'ACTIVE',
          'active_keys': 2,
          'last_request_at': '2026-09-20T10:00:00Z',
        },
        'webhooks': [
          {
            'id': 'w1',
            'url': 'https://hooks.example.com/ecomsbd',
            'state': 'FAILING',
            'failed_24h': 3,
          },
        ],
        'connections': [],
      });
      fake.adapter.onJson('GET', '/developers/deliveries', {
        'items': [
          {
            'id': 'd1',
            'topic': 'order.delivered',
            'status': 'FAILED',
            'last_status': 500,
            'created_at': '2026-09-20T10:00:00Z',
          },
        ],
      });
      fake.adapter.onJson('POST', '/developers/deliveries/d1/retry', {
        'id': 'd1',
        'status': 'PENDING',
      });
      await tester.pumpWidget(_app(fake.client, locale));
      await tester.pumpAndSettle();
      final en = locale.languageCode == 'en';
      expect(
        find.textContaining(en ? 'Ready — 2 active key(s)' : 'প্রস্তুত — 2টি'),
        findsOneWidget,
      );
      expect(find.text('hooks.example.com'), findsOneWidget);
      expect(
        find.textContaining(en ? 'Failing' : 'ব্যর্থ হচ্ছে'),
        findsOneWidget,
      );
      await tester.tap(find.text(en ? 'Retry' : 'আবার চেষ্টা'));
      await tester.pumpAndSettle();
      expect(
        fake.adapter.requests.any(
          (r) => r.path == '/developers/deliveries/d1/retry',
        ),
        isTrue,
      );
    });
  }
}
