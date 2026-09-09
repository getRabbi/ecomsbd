import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../data/auth/auth_controller.dart';
import '../features/auth/otp_verify_screen.dart';
import '../features/auth/phone_login_screen.dart';
import '../features/onboarding/shop_setup_screen.dart';
import '../features/shell/main_shell.dart';
import '../features/splash/splash_screen.dart';
import 'providers.dart';

/// Route paths. Named constants so a typo is a compile error.
class Routes {
  const Routes._();

  static const String splash = '/';
  static const String login = '/login';
  static const String verify = '/login/verify';
  static const String onboarding = '/onboarding';
  static const String home = '/home';
}

/// The router.
///
/// Redirection is driven entirely by [AuthStage], so "which screen may this
/// seller see" is decided in one place. A screen never checks the session
/// itself, which is what stops a half-onboarded account from reaching the
/// dashboard through some path nobody thought about.
final routerProvider = Provider<GoRouter>((ref) {
  final notifier = _AuthRouteNotifier(ref);
  ref.onDispose(notifier.dispose);

  return GoRouter(
    initialLocation: Routes.splash,
    refreshListenable: notifier,
    redirect: (context, state) {
      final stage = ref.read(authControllerProvider).stage;
      final location = state.matchedLocation;

      return switch (stage) {
        AuthStage.restoring => location == Routes.splash ? null : Routes.splash,
        AuthStage.signedOut =>
          location == Routes.login || location == Routes.verify
              ? null
              : Routes.login,
        AuthStage.needsOnboarding =>
          location == Routes.onboarding ? null : Routes.onboarding,
        AuthStage.ready => location == Routes.home ? null : Routes.home,
      };
    },
    routes: <RouteBase>[
      GoRoute(
        path: Routes.splash,
        builder: (context, state) => const SplashScreen(),
      ),
      GoRoute(
        path: Routes.login,
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
            onChangeNumber: () => context.go(Routes.login),
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
    ],
    errorBuilder: (context, state) =>
        const SplashScreen(message: 'Taking you back…'),
  );
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
      if (previous?.stage != next.stage) {
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
