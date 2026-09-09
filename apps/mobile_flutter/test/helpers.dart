import 'package:ecomsbd/design/glass.dart';
import 'package:ecomsbd/design/theme.dart';
import 'package:flutter/material.dart';
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
  return ProviderScope(
    overrides: <Override>[
      // Blur is disabled in tests: `BackdropFilter` is slow in the test
      // renderer and contributes nothing to a layout assertion.
      effectsModeProvider.overrideWith((ref) => effects),
      ...overrides,
    ],
    child: MaterialApp(
      theme: buildEcomsbdTheme(),
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
