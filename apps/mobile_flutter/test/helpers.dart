import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The reference low-end Android screen: 360x800 at 3x.
///
/// Every layout test runs at this size because it is the device master spec
/// section 53 targets, and it is where a four-column grid or an unwrapped money
/// figure actually overflows.
const Size referencePhone = Size(360, 800);

/// A wider phone, to catch layouts that only work at one width.
const Size largePhone = Size(412, 915);

/// Wrap a widget in the app's theme and provider scope.
Widget wrapForTest(
  Widget child, {
  List<Override> overrides = const <Override>[],
  EffectsMode effects = EffectsMode.reduced,
}) {
  // The app has two locale sources and keeps them in step: `Localizations`,
  // which `context.tr` reads, and the `activeAppLocale` global, which the
  // `_t` helpers read from places that have no BuildContext. `LocaleController`
  // sets both. Tests must too — setting only the first left every enum label
  // and model-level string rendering in the other language, which is a
  // difference no real screen can produce.
  activeAppLocale = AppLocale.en;

  return ProviderScope(
    overrides: <Override>[
      // Blur is disabled in tests: `BackdropFilter` is slow in the test
      // renderer and contributes nothing to a layout assertion.
      effectsModeProvider.overrideWith((ref) => effects),
      ...overrides,
    ],
    child: MaterialApp(
      theme: buildEcomsbdTheme(),
      locale: const Locale('en'),
      supportedLocales: const <Locale>[Locale('bn'), Locale('en')],
      localizationsDelegates: const <LocalizationsDelegate<Object>>[
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: child,
      debugShowCheckedModeBanner: false,
    ),
  );
}

/// Render at a fixed device size.
Future<void> pumpAtSize(
  WidgetTester tester,
  Widget widget, {
  Size size = referencePhone,
  double devicePixelRatio = 3.0,
  List<Override> overrides = const <Override>[],
}) async {
  tester.view.physicalSize = size * devicePixelRatio;
  tester.view.devicePixelRatio = devicePixelRatio;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(wrapForTest(widget, overrides: overrides));
  await tester.pump();
}
