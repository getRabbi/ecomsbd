import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:ecomsbd/features/auth/email_auth_screen.dart';
import 'package:ecomsbd/features/auth/verification_screen.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/auth_harness.dart';

void main() {
  late AuthHarness h;
  setUp(() {
    h = AuthHarness();
  });
  tearDown(() {
    h.dispose();
  });

  Future<void> show(WidgetTester tester, Widget screen) async {
    await h.controller.restore();
    await tester.pumpWidget(
      ProviderScope(
        overrides: [authControllerProvider.overrideWith((ref) => h.controller)],
        child: MaterialApp(
          locale: const Locale('en'),
          supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
          localizationsDelegates: const <LocalizationsDelegate<Object>>[
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: screen,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets(
    'Android login offers email and Google; Apple and phone are deferred',
    (tester) async {
      await show(tester, const EmailAuthScreen());
      expect(find.byType(TextFormField), findsNWidgets(2));
      expect(find.textContaining('Google'), findsWidgets);
      expect(find.textContaining('Apple'), findsNothing);
      expect(find.textContaining('phone', findRichText: true), findsNothing);
    },
  );

  testWidgets('iOS login adds Sign in with Apple beside Google and email', (
    tester,
  ) async {
    debugDefaultTargetPlatformOverride = TargetPlatform.iOS;
    try {
      await show(tester, const EmailAuthScreen());
      expect(find.byType(TextFormField), findsNWidgets(2));
      expect(find.textContaining('Google'), findsWidgets);
      expect(find.byKey(const Key('auth-apple')), findsOneWidget);
      expect(find.text('Continue with Apple'), findsOneWidget);

      await tester.tap(find.byKey(const Key('auth-apple')));
      await tester.pumpAndSettle();
      expect(h.provider.requested, <SignInProvider>[SignInProvider.apple]);
    } finally {
      debugDefaultTargetPlatformOverride = null;
    }
  });

  testWidgets('confirmation remains signed out and supports Supabase resend', (
    tester,
  ) async {
    await h.controller.restore();
    expect(
      await h.controller.register('seller@example.com', 'strong password'),
      isTrue,
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [authControllerProvider.overrideWith((ref) => h.controller)],
        child: MaterialApp(
          locale: const Locale('en'),
          supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
          localizationsDelegates: const <LocalizationsDelegate<Object>>[
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          home: VerificationScreen(onContinue: () {}),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(h.controller.state.stage, AuthStage.signedOut);
    expect(find.textContaining('Check seller@example.com'), findsOneWidget);
    await tester.tap(find.text('Resend verification email'));
    await tester.pump();
    expect(h.transport.requests.last.url.path, '/auth/v1/resend');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('missing recovery session cannot show an active reset form', (
    tester,
  ) async {
    await show(
      tester,
      const EmailAuthScreen(mode: EmailAuthMode.resetPassword),
    );
    expect(find.byType(TextFormField), findsNothing);
    expect(h.transport.requests, isEmpty);
  });
}
