import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/messaging/campaigns_screen.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';

Map<String, dynamic> _campaign(String status) => {
  'id': 'c1',
  'name': 'Eid offer',
  'kind': 'ONE_OFF',
  'channel': 'WHATSAPP',
  'status': status,
  'total_recipients': 40,
};

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

Widget _app(_Fake fake, String language, Widget home) => ProviderScope(
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
    home: home,
  ),
);

void _detail(_Fake fake, String Function() status) {
  fake.adapter.on(
    'GET',
    '/campaigns/c1',
    (_) => FakeReply({
      'campaign': _campaign(status()),
      'blocker': null,
      'analytics': {
        'recipients': {'total': 40},
        'sent': 30,
        'delivered': 25,
        'read': null,
        'failed': 2,
        'opt_outs': 1,
        'orders_after': {'window_days': 7, 'orders': 3},
      },
    }),
  );
}

void main() {
  for (final language in ['en', 'bn']) {
    testWidgets('campaigns list shows monitoring in $language', (tester) async {
      final fake = buildFakeApi();
      fake.adapter.onJson('GET', '/campaigns', {
        'items': [_campaign('SENDING')],
      });
      fake.adapter.onJson('GET', '/messaging/overview', {
        'items': [
          {
            'purpose': 'MARKETING',
            'channel': 'WHATSAPP',
            'status': 'DELIVERED',
            'count': 25,
          },
          {
            'purpose': 'TRANSACTIONAL',
            'channel': 'EMAIL',
            'status': 'SENT',
            'count': 4,
          },
        ],
        'marketing_contacts': 120,
        'marketing_opt_outs': 3,
      });
      fake.adapter.onJson('GET', '/campaigns/catalog', {
        'can_pause': true,
        'can_manage': false,
      });
      await tester.pumpWidget(_app(fake, language, const CampaignsScreen()));
      await tester.pumpAndSettle();
      expect(find.text('Eid offer'), findsOneWidget);
      expect(find.text('120'), findsOneWidget);
      expect(find.text('25'), findsOneWidget);
      expect(
        find.textContaining(language == 'en' ? 'Sending' : 'পাঠানো চলছে'),
        findsOneWidget,
      );
    });
  }

  testWidgets('an operator can pause a running campaign', (tester) async {
    final fake = buildFakeApi();
    var status = 'SENDING';
    _detail(fake, () => status);
    fake.adapter.on('POST', '/campaigns/c1/pause', (_) {
      status = 'PAUSED';
      return FakeReply(_campaign(status));
    });
    await tester.pumpWidget(
      _app(
        fake,
        'en',
        const CampaignDetailScreen(
          campaignId: 'c1',
          canPause: true,
          canManage: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Not reported'), findsOneWidget);
    expect(find.text('Resume'), findsNothing);
    expect(find.text('Cancel campaign'), findsNothing);
    await tester.tap(find.text('Pause'));
    await tester.pumpAndSettle();
    expect(fake.adapter.to('POST', '/campaigns/c1/pause').length, 1);
    expect(find.text('Pause'), findsNothing);
    expect(find.textContaining('PAUSED'), findsOneWidget);
  });

  testWidgets('a viewer only watches', (tester) async {
    final fake = buildFakeApi();
    _detail(fake, () => 'SENDING');
    await tester.pumpWidget(
      _app(
        fake,
        'bn',
        const CampaignDetailScreen(
          campaignId: 'c1',
          canPause: false,
          canManage: false,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.byType(FilledButton), findsNothing);
    expect(find.text('জানা যায় না'), findsOneWidget);
    expect(fake.adapter.requests.where((r) => r.method == 'POST'), isEmpty);
  });
}
