import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/shared/network_status.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _app(ProviderContainer container, {Locale locale = const Locale('en')}) {
  return UncontrolledProviderScope(
    container: container,
    child: MaterialApp(
      theme: buildEcomsbdTheme(),
      locale: locale,
      supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
      localizationsDelegates: const <LocalizationsDelegate<Object>>[
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      builder: (context, child) => NetworkStatusFrame(child: child!),
      home: const Scaffold(body: SafeArea(child: TextField())),
    ),
  );
}

void main() {
  testWidgets('the strip shows offline, goes when back, and the screen keeps '
      'what was typed', (tester) async {
    activeAppLocale = AppLocale.en;
    final container = ProviderContainer();
    addTearDown(container.dispose);
    await tester.pumpWidget(_app(container));
    await tester.enterText(find.byType(TextField), 'half an order');

    expect(find.text('No internet connection'), findsNothing);

    container.read(networkMonitorProvider.notifier).reportUnreachable();
    await tester.pumpAndSettle();

    expect(find.text('No internet connection'), findsOneWidget);
    expect(find.text('Check your connection and try again'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
    expect(find.text('half an order'), findsOneWidget);

    container.read(networkMonitorProvider.notifier).reportReachable();
    await tester.pumpAndSettle();

    expect(find.text('No internet connection'), findsNothing);
    expect(find.text('half an order'), findsOneWidget);
  });

  testWidgets('the strip speaks Bangla', (tester) async {
    activeAppLocale = AppLocale.bn;
    addTearDown(() => activeAppLocale = AppLocale.en);
    final container = ProviderContainer();
    addTearDown(container.dispose);
    await tester.pumpWidget(_app(container, locale: const Locale('bn')));

    container.read(networkMonitorProvider.notifier).reportUnreachable();
    await tester.pumpAndSettle();

    expect(find.text('ইন্টারনেট সংযোগ নেই'), findsOneWidget);
    expect(find.text('সংযোগ পরীক্ষা করে আবার চেষ্টা করুন'), findsOneWidget);
  });

  testWidgets('the strip takes the status-bar inset instead of covering the '
      'screen', (tester) async {
    tester.view.padding = const FakeViewPadding(top: 72);
    addTearDown(tester.view.reset);
    final container = ProviderContainer();
    addTearDown(container.dispose);
    await tester.pumpWidget(_app(container));
    final online = tester.getTopLeft(find.byType(TextField)).dy;

    container.read(networkMonitorProvider.notifier).reportUnreachable();
    await tester.pumpAndSettle();

    final strip = tester.getRect(find.byType(OfflineStrip));
    final offline = tester.getTopLeft(find.byType(TextField)).dy;
    expect(strip.top, 0);
    expect(offline, greaterThanOrEqualTo(strip.bottom));
    expect(offline, greaterThan(online));
  });
}
