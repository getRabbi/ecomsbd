import 'dart:async';

import 'package:ecomsbd/data/channels/integration_connect.dart';
import 'package:ecomsbd/data/channels/integration_hub.dart';
import 'package:ecomsbd/features/settings/integration_setup_screens.dart';
import 'package:ecomsbd/l10n/app_strings_data.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

Map<String, dynamic> detail(String state, {String? blocker}) => {
  'connection': {
    'id': 'wa1',
    'provider': 'WHATSAPP',
    'state': state,
    'account_name': 'My shop',
  },
  'availability': {'available': blocker == null, 'blocker': blocker},
};

void main() {
  testWidgets(
    'seller opens hosted signup without manual credentials; return checks backend',
    (tester) async {
      final returns = StreamController<IntegrationReturn>.broadcast();
      final opened = <Uri>[];
      final harness = CommerceHarness(
        extraOverrides: [
          integrationReturnsProvider.overrideWithValue(returns.stream),
          externalOpenerProvider.overrideWithValue((url) async {
            opened.add(url);
            return true;
          }),
        ],
      );
      harness.adapter
        ..onJson('GET', '/integrations', {
          'providers': [
            {'provider': 'WHATSAPP', 'available': true},
          ],
        })
        ..onJson('POST', '/integrations', {
          'connection': {'id': 'wa1'},
        })
        ..onJson('POST', '/integrations/wa1/connect', {
          'authorize_url':
              'https://scalemyprints.com/ecomsbd/whatsapp-connect#state=one-time-state',
        })
        ..onJson('GET', '/integrations/wa1', detail('PENDING'));
      await pumpCommerceScreen(
        tester,
        const WhatsAppConnectScreen(),
        harness: harness,
      );
      expect(find.text('Connect with WhatsApp'), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
      for (final key in ['ics-wa-number', 'ics-wa-waba', 'ics-wa-token']) {
        expect(find.byKey(Key(key)), findsNothing);
      }
      await tester.tap(find.byKey(const Key('ics-wa-connect')));
      await settle(tester, frames: 8);
      expect(opened.single.path, '/ecomsbd/whatsapp-connect');
      expect(find.text('Finish in your browser'), findsOneWidget);
      // A forged success hint cannot make a pending connection look connected.
      returns.add(
        const IntegrationReturn(connectionId: 'wa1', result: 'CONNECTED'),
      );
      await settle(tester, frames: 8);
      expect(find.byKey(const Key('ics-connected')), findsNothing);
      harness.adapter.onJson('GET', '/integrations/wa1', detail('CONNECTED'));
      returns.add(
        const IntegrationReturn(connectionId: 'wa1', result: 'CONNECTED'),
      );
      await settle(tester, frames: 8);
      expect(find.byKey(const Key('ics-connected')), findsOneWidget);
      await returns.close();
    },
  );

  testWidgets('expired connection offers WhatsApp reconnect', (tester) async {
    final harness = CommerceHarness()
      ..adapter.onJson('GET', '/integrations/wa1', detail('AUTH_EXPIRED'));
    await pumpCommerceScreen(
      tester,
      const WhatsAppConnectScreen(connectionId: 'wa1'),
      harness: harness,
    );
    expect(find.text('Reconnect with WhatsApp'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);
  });

  for (final blocker in ['META_APP_SETUP_REQUIRED', 'META_APPROVAL_REQUIRED']) {
    testWidgets('$blocker shows a truthful blocker and no manual fallback', (
      tester,
    ) async {
      final harness = CommerceHarness()
        ..adapter.onJson(
          'GET',
          '/integrations/wa1',
          detail('PENDING', blocker: blocker),
        );
      await pumpCommerceScreen(
        tester,
        const WhatsAppConnectScreen(connectionId: 'wa1'),
        harness: harness,
      );
      expect(find.text(englishStrings['int.code.$blocker']!), findsOneWidget);
      expect(find.byKey(const Key('ics-wa-connect')), findsNothing);
      expect(find.byType(TextField), findsNothing);
    });
  }

  test(
    'configuration, approval, and seller not connected are separate states',
    () {
      expect(
        providerSetupState({
          'available': false,
          'blocker': 'META_APP_SETUP_REQUIRED',
        }),
        IntegrationSetupState.temporarilyUnavailable,
      );
      expect(
        providerSetupState({
          'available': false,
          'blocker': 'META_APPROVAL_REQUIRED',
        }),
        IntegrationSetupState.providerApprovalRequired,
      );
      expect(
        providerSetupState({'available': true}),
        IntegrationSetupState.canConnect,
      );
      expect(banglaStrings['ics.waConnect'], 'WhatsApp দিয়ে যুক্ত করুন');
    },
  );
}
