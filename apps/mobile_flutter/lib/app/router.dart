import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/auth/auth_controller.dart';
import '../core/env.dart';
import '../features/auth/email_auth_screen.dart';
import '../features/auth/verification_screen.dart';
import '../features/auth/otp_verify_screen.dart';
import '../features/auth/phone_login_screen.dart';
import '../features/onboarding/shop_setup_screen.dart';
import '../features/shell/main_shell.dart';
import '../features/splash/splash_screen.dart';
import '../features/billing/plans_screen.dart';
import 'providers.dart';

/// Route paths. Named constants so a typo is a compile error.
class Routes {
  const Routes._();

  static const String splash = '/';
  static const String login = '/login';
  static const String register = '/register';
  static const String forgotPassword = '/forgot-password';
  static const String phoneLogin = '/login/phone';
  static const String verificationPending = '/verify-email';
  static const String emailLink = '/auth/email/verify';
  static const String resetPassword = '/auth/password/reset';
  static const String selectShop = '/choose-shop';
  static const String verify = '/login/verify';
  static const String onboarding = '/onboarding';
  static const String home = '/home';
  static const Set<String> plans = {
    '/plans',
    '/upgrade',
    '/subscription',
    '/pricing',
  };
}

String? authRedirect(
  AuthState auth,
  String location, {
  bool phoneOtpEnabled = Env.phoneOtpLoginEnabled,
}) {
  if (auth.stage == AuthStage.passwordRecovery) {
    return location == Routes.resetPassword ? null : Routes.resetPassword;
  }
  // Emailed bearer links can be completed even without an app session.
  if (location == Routes.resetPassword || location == Routes.emailLink) {
    return null;
  }
  if (auth.stage == AuthStage.restoring) {
    return location == Routes.splash ? null : Routes.splash;
  }
  if (auth.verificationEmail != null) {
    return location == Routes.verificationPending
        ? null
        : Routes.verificationPending;
  }
  return switch (auth.stage) {
    AuthStage.restoring => null,
    AuthStage.passwordRecovery => Routes.resetPassword,
    AuthStage.signedOut =>
      {
            Routes.login,
            Routes.register,
            Routes.forgotPassword,
            if (phoneOtpEnabled) Routes.phoneLogin,
            if (phoneOtpEnabled) Routes.verify,
          }.contains(location)
          ? null
          : Routes.login,
    AuthStage.needsShopSelection =>
      location == Routes.selectShop ? null : Routes.selectShop,
    AuthStage.needsOnboarding =>
      location == Routes.onboarding ? null : Routes.onboarding,
    AuthStage.ready =>
      location == Routes.home || Routes.plans.contains(location)
          ? null
          : Routes.home,
  };
}

String? _linkToken(Uri uri) {
  try {
    final token =
        Uri.splitQueryString(uri.fragment)['token'] ??
        uri.queryParameters['token'];
    return token == null || token.isEmpty ? null : token;
  } on FormatException {
    return null;
  }
}

/// The router.
///
/// Redirection is driven entirely by [AuthStage], so "which screen may this
/// seller see" is decided in one place. A screen never checks the session
/// itself. Accounts without a shop enter the existing onboarding flow; accounts
/// with a shop enter Home (or choose among their existing shops first).
final routerProvider = Provider<GoRouter>((ref) {
  final notifier = _AuthRouteNotifier(ref);
  ref.onDispose(notifier.dispose);

  void clearError() => ref.read(authControllerProvider.notifier).clearError();
  final router = GoRouter(
    initialLocation: Routes.splash,
    refreshListenable: notifier,
    redirect: (context, state) {
      return authRedirect(
        ref.read(authControllerProvider),
        state.matchedLocation,
      );
    },
    routes: <RouteBase>[
      GoRoute(
        path: Routes.splash,
        builder: (context, state) => const SplashScreen(),
      ),
      GoRoute(
        path: Routes.login,
        builder: (context, state) => EmailAuthScreen(
          onCreateAccount: () {
            clearError();
            context.go(Routes.register);
          },
          onForgotPassword: () {
            clearError();
            context.go(Routes.forgotPassword);
          },
          onPhoneLogin: () {
            clearError();
            context.go(Routes.phoneLogin);
          },
        ),
      ),
      GoRoute(
        path: Routes.register,
        builder: (context, state) => EmailAuthScreen(
          mode: EmailAuthMode.register,
          onSignIn: () {
            clearError();
            context.go(Routes.login);
          },
        ),
      ),
      GoRoute(
        path: Routes.forgotPassword,
        builder: (context, state) => EmailAuthScreen(
          mode: EmailAuthMode.forgotPassword,
          onSignIn: () {
            clearError();
            context.go(Routes.login);
          },
        ),
      ),
      GoRoute(
        path: Routes.resetPassword,
        builder: (context, state) => EmailAuthScreen(
          mode: EmailAuthMode.resetPassword,
          resetToken:
              ref.read(authControllerProvider).stage ==
                  AuthStage.passwordRecovery
              ? 'supabase-recovery'
              : null,
          onSignIn: () {
            clearError();
            context.go(Routes.login);
          },
          onForgotPassword: () {
            clearError();
            context.go(Routes.forgotPassword);
          },
        ),
      ),
      GoRoute(
        path: Routes.verificationPending,
        builder: (context, state) =>
            VerificationScreen(onContinue: () => context.go(Routes.login)),
      ),
      GoRoute(
        path: Routes.emailLink,
        builder: (context, state) => VerificationScreen(
          token: _linkToken(state.uri),
          onContinue: () => context.go(Routes.login),
        ),
      ),
      GoRoute(
        path: Routes.selectShop,
        builder: (context, state) => const SelectShopScreen(),
      ),
      GoRoute(
        path: Routes.phoneLogin,
        builder: (context, state) => PhoneLoginScreen(
          onCodeSent: (challengeId, maskedPhone, debugCode) {
            context.push(
              Routes.verify,
              extra: OtpRouteArgs(
                challengeId: challengeId,
                maskedPhone: maskedPhone,
                debugCode: debugCode,
              ),
            );
          },
        ),
      ),
      GoRoute(
        path: Routes.verify,
        builder: (context, state) {
          final args = state.extra as OtpRouteArgs?;
          if (args == null) {
            // Reached without a challenge (a deep link, or a restart mid-flow).
            // Sending the seller back to enter their number is the only honest
            // option: there is no code to verify.
            return PhoneLoginScreen(
              onCodeSent: (challengeId, maskedPhone, debugCode) => context.push(
                Routes.verify,
                extra: OtpRouteArgs(
                  challengeId: challengeId,
                  maskedPhone: maskedPhone,
                  debugCode: debugCode,
                ),
              ),
            );
          }
          return OtpVerifyScreen(
            challengeId: args.challengeId,
            maskedPhone: args.maskedPhone,
            debugCode: args.debugCode,
            onVerified: () {
              // The redirect rule decides where to land next, based on whether
              // the account has a shop.
              ref.read(authControllerProvider);
            },
            onChangeNumber: () => context.go(Routes.phoneLogin),
          );
        },
      ),
      GoRoute(
        path: Routes.onboarding,
        builder: (context, state) =>
            ShopSetupScreen(onComplete: () => context.go(Routes.home)),
      ),
      GoRoute(
        path: Routes.home,
        builder: (context, state) => const MainShell(),
      ),
      for (final path in Routes.plans)
        GoRoute(path: path, builder: (context, state) => const PlansScreen()),
    ],
    errorBuilder: (context, state) =>
        const SplashScreen(message: 'Taking you back…'),
  );
  ref.onDispose(router.dispose);
  return router;
});

/// Arguments passed from the phone screen to the OTP screen.
class OtpRouteArgs {
  const OtpRouteArgs({
    required this.challengeId,
    required this.maskedPhone,
    this.debugCode,
  });

  final String challengeId;
  final String maskedPhone;
  final String? debugCode;
}

/// Bridges Riverpod state changes into `GoRouter.refreshListenable`.
class _AuthRouteNotifier extends ChangeNotifier {
  _AuthRouteNotifier(Ref ref) {
    _subscription = ref.listen<AuthState>(authControllerProvider, (
      previous,
      next,
    ) {
      if (previous?.stage != next.stage ||
          previous?.verificationEmail != next.verificationEmail) {
        notifyListeners();
      }
    });
  }

  late final ProviderSubscription<AuthState> _subscription;

  @override
  void dispose() {
    _subscription.close();
    super.dispose();
  }
}
