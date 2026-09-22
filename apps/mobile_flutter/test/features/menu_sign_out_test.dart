import 'dart:async';

import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/data/auth/auth_controller.dart';
import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/menu/menu_overlay.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import '../data/auth_harness.dart';

/// Signs out the way a device does: Supabase reports the sign-out first, the
/// router redirects to login, and only later does `signOut()` return.
class _SlowSignOut extends AuthController {
  _SlowSignOut(super.repository) : super(providerSignIn: FakeProviderSignIn());

  final finish = Completer<void>();

  @override
  Future<void> signOut({bool allDevices = false}) async {
    state = const AuthState(stage: AuthStage.signedOut);
    await finish.future;
  }
}

void main() {
  testWidgets('signing out from the menu lands on login, not a blank screen', (
    tester,
  ) async {
    final harness = AuthHarness();
    addTearDown(harness.dispose);
    final controller = _SlowSignOut(harness.repository);
    final refresh = ValueNotifier<int>(0);
    addTearDown(refresh.dispose);
    var stage = AuthStage.restoring;
    controller.addListener((state) {
      stage = state.stage;
      refresh.value++;
    });

    final router = GoRouter(
      initialLocation: '/home',
      refreshListenable: refresh,
      redirect: (context, state) =>
          stage == AuthStage.signedOut && state.matchedLocation != '/login'
          ? '/login'
          : null,
      routes: <RouteBase>[
        GoRoute(
          path: '/login',
          builder: (context, state) => const Scaffold(body: Text('login page')),
        ),
        GoRoute(
          path: '/home',
          builder: (context, state) => Scaffold(
            body: Builder(
              builder: (context) => TextButton(
                onPressed: () => MenuOverlay.show(context),
                child: const Text('open menu'),
              ),
            ),
          ),
        ),
      ],
    );
    addTearDown(router.dispose);

    activeAppLocale = AppLocale.en;
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          effectsModeProvider.overrideWith((ref) => EffectsMode.reduced),
          authControllerProvider.overrideWith((ref) => controller),
        ],
        child: MaterialApp.router(
          theme: buildEcomsbdTheme(),
          locale: const Locale('en'),
          supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
          localizationsDelegates: const <LocalizationsDelegate<Object>>[
            AppStrings.delegate,
            GlobalMaterialLocalizations.delegate,
            GlobalWidgetsLocalizations.delegate,
            GlobalCupertinoLocalizations.delegate,
          ],
          routerConfig: router,
        ),
      ),
    );

    await tester.tap(find.text('open menu'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Sign out'),
      300,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.tap(find.text('Sign out'));
    await tester.pumpAndSettle();

    controller.finish.complete();
    await tester.pumpAndSettle();

    expect(find.text('login page'), findsOneWidget);
    expect(find.text('Sign out'), findsNothing);
  });
}
