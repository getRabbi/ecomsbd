import 'package:ecomsbd/features/billing/plans_screen.dart';
import 'package:ecomsbd/app/router.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/features/menu/more_screen.dart';
import 'package:ecomsbd/features/shared/data_state.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'commerce_harness.dart';

CommerceHarness launchHarness({bool enabled = true}) =>
    CommerceHarness()
      ..adapter.onJson('GET', '/billing/entitlements', <String, dynamic>{
        'plan': 'free',
        'status': 'ACTIVE',
        'free_launch_mode': enabled,
        'billing_enabled': false,
        'effective_access': enabled ? 'full_access' : 'plan',
        'entitlements': <String, dynamic>{'advanced_profit': enabled},
      });

void main() {
  test(
    'stale plan links keep authentication and reach the guarded plans screen',
    () {
      for (final path in Routes.plans) {
        expect(
          authRedirect(const AuthState(stage: AuthStage.ready), path),
          isNull,
        );
        expect(
          authRedirect(const AuthState(stage: AuthStage.signedOut), path),
          Routes.login,
        );
      }
    },
  );
  for (final locale in <AppLocale>[AppLocale.en, AppLocale.bn]) {
    testWidgets('old plans route is informative in ${locale.name}', (
      tester,
    ) async {
      final strings = AppStrings(locale);
      await pumpCommerceScreen(
        tester,
        Builder(
          builder: (context) => Localizations.override(
            context: context,
            locale: Locale(locale.name),
            child: const PlansScreen(),
          ),
        ),
        harness: launchHarness(),
      );
      expect(find.text(strings.t('launch.fullAccess')), findsOneWidget);
      expect(find.text(strings.t('launch.message')), findsOneWidget);
      expect(find.text(strings.t('pl.plans')), findsNothing);
      expect(find.text('Pro'), findsNothing);
      expectNoOverflow(tester);
    });
  }

  testWidgets('More navigation hides subscription during launch', (
    tester,
  ) async {
    await pumpCommerceScreen(
      tester,
      const MoreScreen(),
      harness: launchHarness(),
    );
    await tester.enterText(find.byType(TextField), 'subscr');
    await settle(tester);
    expect(find.text('Subscription'), findsNothing);
  });

  testWidgets('turning launch off restores subscription navigation', (
    tester,
  ) async {
    await pumpCommerceScreen(
      tester,
      const MoreScreen(),
      harness: launchHarness(enabled: false),
    );
    await tester.enterText(find.byType(TextField), 'subscr');
    await settle(tester);
    expect(find.text('Subscription'), findsOneWidget);
  });

  testWidgets('a stale 402 notice contains no paywall or plans CTA', (
    tester,
  ) async {
    await pumpCommerceScreen(
      tester,
      const PlanLockedNotice(),
      harness: launchHarness(),
    );
    expect(find.text('Not included in your plan'), findsNothing);
    expect(find.text('See plans'), findsNothing);
    expect(
      find.text('All ecomsbd features are currently available for free.'),
      findsOneWidget,
    );
  });

  testWidgets('turning launch off restores the paywall', (tester) async {
    await pumpCommerceScreen(
      tester,
      const PlanLockedNotice(),
      harness: launchHarness(enabled: false),
    );
    expect(find.text('Not included in your plan'), findsOneWidget);
  });
}
