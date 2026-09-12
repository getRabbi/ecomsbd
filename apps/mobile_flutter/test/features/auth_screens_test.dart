import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/app/router.dart';
import 'package:ecomsbd/data/auth/provider_sign_in.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/auth/email_auth_screen.dart';
import 'package:ecomsbd/features/auth/verification_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../data/auth_harness.dart';
import '../helpers.dart';

void main() {
  late AuthHarness h;
  setUp(() {
    PackageInfo.setMockInitialValues(
      appName: 'ecomsbd',
      packageName: 'test.ecomsbd',
      version: '1.0.0',
      buildNumber: '1',
      buildSignature: '',
    );
    h = AuthHarness();
  });
  tearDown(() => h.dispose());

  Future<void> show(WidgetTester tester, Widget screen) async {
    await h.controller.restore();
    await pumpAtSize(
      tester,
      screen,
      overrides: [authControllerProvider.overrideWith((ref) => h.controller)],
    );
  }

  Future<void> submit(WidgetTester tester) async {
    await tester.ensureVisible(find.byKey(const Key('auth-submit')));
    await tester.tap(find.byKey(const Key('auth-submit')));
    await tester.pumpAndSettle();
  }

  Future<ProviderContainer> showRouter(WidgetTester tester) async {
    await h.controller.restore();
    final container = ProviderContainer(
      overrides: [authControllerProvider.overrideWith((ref) => h.controller)],
    );
    addTearDown(container.dispose);
    await tester.pumpWidget(
      UncontrolledProviderScope(
        container: container,
        child: MaterialApp.router(
          theme: buildEcomsbdTheme(),
          routerConfig: container.read(routerProvider),
        ),
      ),
    );
    await tester.pumpAndSettle();
    return container;
  }

  testWidgets(
    'router navigates login, register and recovery without stale form state',
    (tester) async {
      final container = await showRouter(tester);
      await tester.ensureVisible(find.text('Create account'));
      await tester.tap(find.text('Create account'));
      await tester.pumpAndSettle();
      expect(find.byKey(const Key('auth-confirm')), findsOneWidget);
      await tester.tap(find.text('Back to Sign In'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Forgot password'));
      await tester.tap(find.text('Forgot password'));
      await tester.pumpAndSettle();
      h.adapter.onJson('POST', '/auth/password/forgot', {'ok': true});
      await tester.enterText(
        find.byKey(const Key('auth-email')),
        'seller@example.com',
      );
      await submit(tester);
      expect(find.text('Check your email'), findsOneWidget);
      await tester.tap(find.text('Back to Sign In'));
      await tester.pumpAndSettle();
      expect(find.text('Welcome back'), findsOneWidget);
      expect(find.byKey(const Key('auth-password')), findsOneWidget);
      container.read(routerProvider).go(Routes.phoneLogin);
      await tester.pumpAndSettle();
      expect(find.text('Welcome back'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'router passes a reset-link fragment to the backend then returns to login',
    (tester) async {
      final container = await showRouter(tester);
      h.adapter.onJson('POST', '/auth/password/reset', {'ok': true});
      container
          .read(routerProvider)
          .go('${Routes.resetPassword}#token=fragment-token');
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const Key('auth-password')),
        'new secret phrase',
      );
      await tester.enterText(
        find.byKey(const Key('auth-confirm')),
        'new secret phrase',
      );
      await submit(tester);
      expect(
        h.adapter.to('POST', '/auth/password/reset').single.jsonBody['token'],
        'fragment-token',
      );
      expect(find.text('Password updated'), findsOneWidget);
      await tester.tap(find.text('Back to Sign In'));
      await tester.pumpAndSettle();
      expect(find.text('Welcome back'), findsOneWidget);
    },
  );

  testWidgets(
    'login shows all final methods and hides phone OTP on a small phone',
    (tester) async {
      await show(
        tester,
        EmailAuthScreen(
          onCreateAccount: () {},
          onForgotPassword: () {},
          onPhoneLogin: () {},
        ),
      );
      for (final label in [
        'Continue with Google',
        'Continue with Apple',
        'Email',
        'Password',
        'Sign In',
        'Create account',
        'Forgot password',
      ]) {
        expect(find.text(label), findsOneWidget);
      }
      expect(find.text('Sign in with phone'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('registration rejects mismatch before making any auth request', (
    tester,
  ) async {
    await show(tester, const EmailAuthScreen(mode: EmailAuthMode.register));
    await tester.enterText(
      find.byKey(const Key('auth-email')),
      'new@example.com',
    );
    await tester.enterText(
      find.byKey(const Key('auth-password')),
      'strong secret phrase',
    );
    await tester.enterText(
      find.byKey(const Key('auth-confirm')),
      'different secret',
    );
    await submit(tester);
    expect(find.text('Passwords do not match.'), findsOneWidget);
    expect(h.adapter.requests, isEmpty);
    expect(tester.takeException(), isNull);
  });

  for (final provider in SignInProvider.values) {
    testWidgets(
      '${provider.name} cancellation is understandable and allows retry',
      (tester) async {
        h.provider.error = providerSignInError(provider, cancelled: true);
        await show(tester, const EmailAuthScreen());
        await tester.tap(
          find.text(
            provider == SignInProvider.google
                ? 'Continue with Google'
                : 'Continue with Apple',
          ),
        );
        await tester.pumpAndSettle();
        expect(find.textContaining('was cancelled'), findsOneWidget);
        expect(h.controller.state.isBusy, isFalse);
        expect(h.adapter.requests, isEmpty);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('wrong credentials do not render raw backend details', (
    tester,
  ) async {
    h.fail('/auth/login', 'INVALID_CREDENTIALS');
    await show(tester, const EmailAuthScreen());
    await tester.enterText(
      find.byKey(const Key('auth-email')),
      'seller@example.com',
    );
    await tester.enterText(
      find.byKey(const Key('auth-password')),
      'wrong password',
    );
    await submit(tester);
    expect(find.textContaining('do not match'), findsOneWidget);
    expect(find.textContaining('private'), findsNothing);
  });

  testWidgets('forgot password shows the same non-enumerating confirmation', (
    tester,
  ) async {
    h.adapter.onJson('POST', '/auth/password/forgot', {'ok': true});
    await show(
      tester,
      const EmailAuthScreen(mode: EmailAuthMode.forgotPassword),
    );
    await tester.enterText(
      find.byKey(const Key('auth-email')),
      'seller@example.com',
    );
    await submit(tester);
    expect(find.text('Check your email'), findsOneWidget);
    expect(
      find.textContaining('If an account uses this email'),
      findsOneWidget,
    );
    expect(find.text('Back to Sign In'), findsOneWidget);
  });

  testWidgets(
    'reset link submits both matching passwords with the bearer token',
    (tester) async {
      h.adapter.onJson('POST', '/auth/password/reset', {'ok': true});
      await show(
        tester,
        const EmailAuthScreen(
          mode: EmailAuthMode.resetPassword,
          resetToken: 'link-token',
        ),
      );
      expect(find.byKey(const Key('auth-email')), findsNothing);
      await tester.enterText(
        find.byKey(const Key('auth-password')),
        'new strong secret',
      );
      await tester.enterText(
        find.byKey(const Key('auth-confirm')),
        'new strong secret',
      );
      await submit(tester);
      expect(find.text('Password updated'), findsOneWidget);
      expect(h.adapter.to('POST', '/auth/password/reset').single.jsonBody, {
        'token': 'link-token',
        'new_password': 'new strong secret',
      });
    },
  );

  testWidgets('incomplete reset link offers recovery, not a broken submit', (
    tester,
  ) async {
    await show(
      tester,
      EmailAuthScreen(
        mode: EmailAuthMode.resetPassword,
        onForgotPassword: () {},
      ),
    );
    expect(
      find.text('This reset link is incomplete. Request a new one.'),
      findsOneWidget,
    );
    expect(find.byKey(const Key('auth-submit')), findsNothing);
  });

  testWidgets(
    'verification pending supports resend with cooldown and continue',
    (tester) async {
      await h.controller.restore();
      h.profile(tenantId: null, needsOnboarding: true);
      h.adapter.onJson('POST', '/auth/register', {
        'session': sessionReply(tenantId: null),
        'email_verification_sent': true,
      });
      // Complete the HTTP future outside the widget fake clock.
      await tester.runAsync(
        () => h.controller.register('new@example.com', 'strong secret phrase'),
      );
      h.adapter.onJson('POST', '/auth/email/resend', {'ok': true});
      var continued = false;
      await pumpAtSize(
        tester,
        VerificationScreen(onContinue: () => continued = true),
        overrides: [authControllerProvider.overrideWith((ref) => h.controller)],
      );
      expect(find.textContaining('new@example.com'), findsOneWidget);
      await tester.tap(find.text('Resend verification email'));
      await tester.pumpAndSettle();
      expect(find.text('Resend in 30 seconds'), findsOneWidget);
      expect(h.adapter.to('POST', '/auth/email/resend'), hasLength(1));
      await tester.tap(find.text('Continue to shop'));
      await tester.pump();
      expect(continued, isTrue);
      expect(h.controller.state.verificationEmail, isNull);
      await tester.pumpWidget(
        const SizedBox.shrink(),
      ); // Disposes the cooldown timer.
    },
  );

  testWidgets(
    'verification link displays success only after backend acceptance',
    (tester) async {
      h.adapter.onJson('POST', '/auth/email/verify', {'ok': true});
      await show(
        tester,
        VerificationScreen(token: 'verify-token', onContinue: () {}),
      );
      expect(find.text('Email verified'), findsNothing);
      await tester.tap(find.text('Verify email'));
      await tester.pumpAndSettle();
      expect(find.text('Email verified'), findsOneWidget);
      expect(h.adapter.to('POST', '/auth/email/verify').single.jsonBody, {
        'token': 'verify-token',
      });
    },
  );
}
