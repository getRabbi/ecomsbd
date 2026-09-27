import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/data/analytics/analytics_providers.dart';
import 'package:ecomsbd/design/components/navigation.dart';
import 'package:ecomsbd/design/components/pills.dart';
import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:ecomsbd/features/menu/menu_overlay.dart';
import 'package:ecomsbd/features/menu/more_screen.dart';
import 'package:ecomsbd/features/shell/main_shell.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/auth_harness.dart';
import '../helpers.dart';
import 'commerce_harness.dart';

/// The signed-in shell's chrome: the top bar, the bottom bar, and the bell.
///
/// The claims under test are the navigation contract:
///
/// * the bottom bar carries the five destinations — Home, Orders, Inbox,
///   Money, More — and More replaces the old hamburger menu;
/// * the section title beside the brand mark is a label and opens nothing;
/// * the unread badge reads 1–9, 10–99 and then 99+, and sits wholly inside the
///   action pill, which clips anything that hangs outside it.
///
/// The product fonts are loaded for real, so the title and badge are measured
/// the way a device measures them rather than in the test font's square glyphs.

const Size _smallPhone = Size(320, 640);

Future<void> _loadFont(String family, List<String> files) async {
  final loader = FontLoader(family);
  for (final file in files) {
    loader.addFont(rootBundle.load('assets/fonts/$file'));
  }
  await loader.load();
}

Future<void> _pumpShell(
  WidgetTester tester, {
  int unread = 0,
  AppLocale locale = AppLocale.en,
  Size size = referencePhone,
}) async {
  final commerce = CommerceHarness();
  final auth = AuthHarness();
  tester.view.physicalSize = size * 3;
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  addTearDown(() async {
    await settle(tester, frames: 3);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump();
    await commerce.dispose();
    auth.dispose();
    activeAppLocale = AppLocale.en;
  });

  activeAppLocale = locale;
  await tester.pumpWidget(
    ProviderScope(
      // A fresh container per pump, so a second shell in one test does not
      // inherit the first one's fake server.
      key: UniqueKey(),
      overrides: <Override>[
        effectsModeProvider.overrideWith((ref) => EffectsMode.reduced),
        ...commerce.overrides,
        // The menu reads the signed-in profile.
        authControllerProvider.overrideWith((ref) => auth.controller),
        unreadNotificationCountProvider.overrideWith((ref) async => unread),
      ],
      child: MaterialApp(
        theme: buildEcomsbdTheme(),
        locale: locale.locale,
        supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
        localizationsDelegates: const <LocalizationsDelegate<Object>>[
          AppStrings.delegate,
          GlobalMaterialLocalizations.delegate,
          GlobalWidgetsLocalizations.delegate,
          GlobalCupertinoLocalizations.delegate,
        ],
        home: const MainShell(),
      ),
    ),
  );
  await settle(tester, frames: 6, step: const Duration(milliseconds: 50));
}

Finder get _bottomBar => find.byType(BottomGlassNavigation);

Finder get _title => find.byType(BrandPill);

/// Switch tab by icon, so the same step works in either language.
Future<void> _openTab(WidgetTester tester, MainDestination tab) async {
  await tester.tap(
    find.descendant(of: _bottomBar, matching: find.byIcon(tab.icon)),
  );
  await settle(tester, frames: 6, step: const Duration(milliseconds: 50));
}

void main() {
  setUpAll(() async {
    await _loadFont('RedditSans', <String>[
      'RedditSans-400.ttf',
      'RedditSans-500.ttf',
      'RedditSans-600.ttf',
      'RedditSans-700.ttf',
      'RedditSans-800.ttf',
    ]);
    await _loadFont('NotoSansBengali', <String>[
      'NotoSansBengali-400.ttf',
      'NotoSansBengali-600.ttf',
      'NotoSansBengali-700.ttf',
    ]);
  });

  group('Bottom bar', () {
    testWidgets('has the five destinations and no Menu tab', (tester) async {
      await _pumpShell(tester);

      for (final label in <String>[
        'Home',
        'Orders',
        'Inbox',
        'Money',
        'More',
      ]) {
        expect(
          find.descendant(of: _bottomBar, matching: find.text(label)),
          findsOneWidget,
          reason: '$label is a tab',
        );
      }
      expect(
        find.descendant(of: _bottomBar, matching: find.text('Menu')),
        findsNothing,
      );
      expect(
        find.descendant(of: _bottomBar, matching: find.byIcon(Icons.menu)),
        findsNothing,
      );
      expect(MainDestination.values, hasLength(5));
    });

    for (final size in <Size>[referencePhone, largePhone, _smallPhone]) {
      testWidgets('spreads the five tabs evenly, with the active one centred '
          '(${size.width.toInt()}dp)', (tester) async {
        await _pumpShell(tester, size: size);

        final slots = find.descendant(
          of: _bottomBar,
          matching: find.byType(Expanded),
        );
        expect(slots, findsNWidgets(5), reason: 'no empty sixth slot');
        final rects = <Rect>[
          for (var i = 0; i < 5; i++) tester.getRect(slots.at(i)),
        ];
        for (final rect in rects) {
          expect(rect.width, closeTo(rects.first.width, 0.01));
        }
        for (var i = 1; i < 5; i++) {
          expect(rects[i].left, closeTo(rects[i - 1].right, 0.01));
        }

        // The white tile behind the active tab.
        final raised = find.descendant(
          of: _bottomBar,
          matching: find.byKey(BottomGlassNavigation.activeTileKey),
        );
        for (final tab in MainDestination.values) {
          await _openTab(tester, tab);
          expect(
            tester.getCenter(raised).dx,
            closeTo(rects[tab.index].center.dx, 0.5),
            reason: '${tab.name} indicator',
          );
        }
        expect(tester.takeException(), isNull);
      });
    }

    testWidgets('switches tabs and shows the section title', (tester) async {
      await _pumpShell(tester);

      await _openTab(tester, MainDestination.money);
      expect(
        find.descendant(of: _title, matching: find.text('Money')),
        findsOneWidget,
      );
      await _openTab(tester, MainDestination.orders);
      expect(
        find.descendant(of: _title, matching: find.text('Orders')),
        findsOneWidget,
      );
      expect(find.byType(MenuOverlay), findsNothing);
    });
  });

  group('Top bar', () {
    testWidgets('More is a tab, so no tab carries a hamburger', (tester) async {
      await _pumpShell(tester);

      for (final tab in MainDestination.values) {
        await _openTab(tester, tab);
        expect(find.byTooltip('Menu'), findsNothing, reason: tab.name);
      }
      await _openTab(tester, MainDestination.more);
      expect(find.byType(MoreScreen), findsOneWidget);
      expect(find.byType(MenuOverlay), findsNothing);
    });

    testWidgets('the section title is a label, not a way into the menu', (
      tester,
    ) async {
      await _pumpShell(tester);

      for (final tab in MainDestination.values) {
        await _openTab(tester, tab);

        // No dropdown chevron and nothing tappable in the title pill.
        expect(
          find.descendant(
            of: _title,
            matching: find.byIcon(Icons.keyboard_arrow_down_rounded),
          ),
          findsNothing,
        );
        expect(
          find.descendant(of: _title, matching: find.byType(InkWell)),
          findsNothing,
        );

        await tester.tap(_title, warnIfMissed: false);
        await tester.tap(
          find.descendant(of: _title, matching: find.byType(Text)),
          warnIfMissed: false,
        );
        await settle(tester, frames: 8, step: const Duration(milliseconds: 50));
        expect(find.byType(MenuOverlay), findsNothing, reason: tab.name);
      }
    });

    for (final locale in AppLocale.values) {
      testWidgets(
        'every tab title fits and lines up at 360dp (${locale.name})',
        (tester) async {
          await _pumpShell(tester, locale: locale, unread: 150);

          Rect? first;
          for (final tab in MainDestination.values) {
            await _openTab(tester, tab);
            final text = find.descendant(
              of: _title,
              matching: find.byType(RichText),
            );
            final paragraph = tester.renderObject<RenderParagraph>(text);
            expect(
              paragraph.didExceedMaxLines,
              isFalse,
              reason: '${tab.name} title is cut off in ${locale.name}',
            );
            final pill = tester.getRect(_title);
            first ??= pill;
            expect(pill.left, closeTo(first.left, 0.01));
            expect(pill.center.dy, closeTo(first.center.dy, 0.01));
          }
          expect(tester.takeException(), isNull);
        },
      );
    }
  });

  group('Notification badge', () {
    testWidgets('is absent when nothing is unread', (tester) async {
      await _pumpShell(tester);
      expect(find.byType(NotificationCountBadge), findsNothing);
    });

    test('counts 1–9, 10–99, then 99+', () {
      expect(NotificationCountBadge.labelFor(1), '1');
      expect(NotificationCountBadge.labelFor(9), '9');
      expect(NotificationCountBadge.labelFor(12), '12');
      expect(NotificationCountBadge.labelFor(99), '99');
      expect(NotificationCountBadge.labelFor(100), '99+');
      expect(NotificationCountBadge.labelFor(4521), '99+');
    });

    for (final locale in AppLocale.values) {
      for (final size in <Size>[referencePhone, largePhone, _smallPhone]) {
        for (final (count, label) in <(int, String)>[
          (1, '1'),
          (12, '12'),
          (150, '99+'),
        ]) {
          testWidgets('shows "$label" whole inside the bar '
              '(${locale.name}, ${size.width.toInt()}dp)', (tester) async {
            await _pumpShell(tester, unread: count, locale: locale, size: size);

            final badge = find.byType(NotificationCountBadge);
            expect(badge, findsOneWidget);
            expect(
              find.descendant(of: badge, matching: find.text(label)),
              findsOneWidget,
            );

            final badgeRect = tester.getRect(badge);
            // The action pill clips to its outline, so the badge must sit
            // inside it — and clear of its 1dp border — to be seen whole.
            final pillRect = tester.getRect(
              find.ancestor(of: badge, matching: find.byType(ClipRRect)).first,
            );
            expect(badgeRect.top, greaterThanOrEqualTo(pillRect.top + 2));
            expect(badgeRect.left, greaterThanOrEqualTo(pillRect.left));
            expect(badgeRect.right, lessThanOrEqualTo(pillRect.right));
            expect(badgeRect.bottom, lessThanOrEqualTo(pillRect.bottom - 2));

            // And inside the bell's own 48dp square, so it never reaches
            // into the neighbouring button.
            final bell = tester.getRect(
              find.ancestor(
                of: badge,
                matching: find.byWidgetPredicate(
                  (widget) =>
                      widget is SizedBox &&
                      widget.width == 48 &&
                      widget.height == 48,
                ),
              ),
            );
            expect(badgeRect.top, greaterThanOrEqualTo(bell.top));
            expect(badgeRect.right, lessThanOrEqualTo(bell.right));
            expect(badgeRect.height, NotificationCountBadge.height);
            expect(
              badgeRect.width,
              greaterThanOrEqualTo(NotificationCountBadge.height),
            );

            // The count is centred in the capsule.
            final textRect = tester.getRect(
              find.descendant(of: badge, matching: find.text(label)),
            );
            expect(textRect.center.dx, closeTo(badgeRect.center.dx, 0.75));
            expect(textRect.center.dy, closeTo(badgeRect.center.dy, 0.75));
            expect(textRect.width, lessThanOrEqualTo(badgeRect.width));

            // The count is still read out, from the bell's tooltip.
            expect(find.byTooltip(RegExp('$count')), findsOneWidget);
            expect(tester.takeException(), isNull);
          });
        }
      }
    }
  });
}
