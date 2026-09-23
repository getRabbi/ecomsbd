import 'dart:ui' show SemanticsFlag;

import 'package:ecomsbd/design/components/pills.dart';
import 'package:ecomsbd/features/splash/splash_screen.dart';
import 'package:ecomsbd/widgets/brand/ecoms_animated_logo.dart';
import 'package:ecomsbd/widgets/brand/ecoms_brand_mark.dart';
import 'package:ecomsbd/widgets/brand/ecoms_logo_loader.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../helpers.dart';

Widget _centred(Widget child) => Scaffold(body: Center(child: child));

void main() {
  group('EcomsAnimatedLogo', () {
    testWidgets('draws the supplied brand mark, decoded at display size', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const EcomsAnimatedLogo()));

      final image =
          tester.widget<Image>(find.byType(Image)).image as ResizeImage;
      expect(
        (image.imageProvider as AssetImage).assetName,
        'assets/icon/logo_mark.png',
      );
      // 110 dp at the reference 3x density, not the 1254 px source.
      expect(image.width, 330);
    });

    testWidgets('keeps hopping while looping', (tester) async {
      await pumpAtSize(tester, _centred(const EcomsAnimatedLogo()));
      await tester.pump(EcomsAnimatedLogo.period * 3);
      expect(tester.hasRunningAnimations, isTrue);
    });

    testWidgets('a one-shot entrance comes to rest', (tester) async {
      await pumpAtSize(
        tester,
        _centred(const EcomsAnimatedLogo(looping: false)),
      );
      expect(tester.hasRunningAnimations, isTrue);
      // Would time out if the hop repeated.
      await tester.pumpAndSettle();
      expect(tester.hasRunningAnimations, isFalse);
    });

    testWidgets('lands the current hop when looping is turned off', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const EcomsAnimatedLogo()));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.pumpWidget(
        wrapForTest(_centred(const EcomsAnimatedLogo(looping: false))),
      );
      expect(tester.hasRunningAnimations, isTrue);
      await tester.pumpAndSettle();
      expect(tester.hasRunningAnimations, isFalse);
    });

    testWidgets('stands still when animate is false', (tester) async {
      await pumpAtSize(
        tester,
        _centred(const EcomsAnimatedLogo(animate: false)),
      );
      expect(tester.hasRunningAnimations, isFalse);
    });

    testWidgets('stands still when the system turns animations off', (
      tester,
    ) async {
      tester.platformDispatcher.accessibilityFeaturesTestValue =
          const FakeAccessibilityFeatures(disableAnimations: true);
      addTearDown(
        tester.platformDispatcher.clearAccessibilityFeaturesTestValue,
      );

      await pumpAtSize(tester, _centred(const EcomsAnimatedLogo()));
      expect(tester.hasRunningAnimations, isFalse);
    });

    testWidgets('the loader is announced to screen readers', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(tester, _centred(const EcomsLogoLoader()));
      expect(find.bySemanticsLabel('Loading'), findsOneWidget);
      handle.dispose();
    });
  });

  group('EcomsBrandMark', () {
    testWidgets('draws the supplied brand mark in exactly its box', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const EcomsBrandMark(size: 30)));

      expect(tester.getSize(find.byType(EcomsBrandMark)), const Size(30, 30));
      final image =
          tester.widget<Image>(find.byType(Image)).image as ResizeImage;
      expect(
        (image.imageProvider as AssetImage).assetName,
        EcomsAnimatedLogo.assetPath,
      );
      // Decoded near display size at 3x, not the 1254 px source.
      expect(image.width, inInclusiveRange(90, 120));
    });

    testWidgets('never animates', (tester) async {
      await pumpAtSize(tester, _centred(const EcomsBrandMark(size: 30)));
      await tester.pump(const Duration(seconds: 2));
      expect(tester.hasRunningAnimations, isFalse);
      expect(find.byType(EcomsAnimatedLogo), findsNothing);
    });

    testWidgets('announces its label when given one', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(
        tester,
        _centred(const EcomsBrandMark(size: 30, semanticLabel: 'ecomsbd')),
      );
      expect(find.bySemanticsLabel('ecomsbd'), findsOneWidget);
      handle.dispose();
    });

    testWidgets('is hidden from screen readers by default', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(tester, _centred(const EcomsBrandMark(size: 30)));
      expect(find.semantics.byFlag(SemanticsFlag.isImage), findsNothing);
      handle.dispose();
    });
  });

  group('BrandPill', () {
    testWidgets('shows the brand mark, not a placeholder letter', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const BrandPill()));
      expect(find.byType(EcomsBrandMark), findsOneWidget);
      expect(find.byType(EcomsAnimatedLogo), findsNothing);
      expect(find.text('ecomsbd'), findsOneWidget);
      expect(find.text('e'), findsNothing);
    });

    testWidgets('keeps the brand mark on section tabs', (tester) async {
      await pumpAtSize(
        tester,
        _centred(const BrandPill(label: 'Orders')),
      );
      expect(find.byType(EcomsBrandMark), findsOneWidget);
      expect(find.text('Orders'), findsOneWidget);
      expect(find.text('o'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('reads the product name once, with no image or letter', (
      tester,
    ) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(tester, _centred(const BrandPill()));
      expect(find.semantics.byLabel('ecomsbd'), findsOne);
      expect(find.semantics.byLabel(RegExp(r'^e$')), findsNothing);
      expect(find.semantics.byFlag(SemanticsFlag.isImage), findsNothing);
      handle.dispose();
    });
  });

  group('SplashScreen', () {
    testWidgets('shows the animated brand mark', (tester) async {
      await pumpAtSize(tester, const SplashScreen());
      expect(find.byType(EcomsLogoLoader), findsOneWidget);
      expect(tester.takeException(), isNull);
    });

    testWidgets('keeps its message under the mark', (tester) async {
      await pumpAtSize(tester, const SplashScreen(message: 'Taking you back…'));
      expect(find.byType(EcomsLogoLoader), findsOneWidget);
      expect(find.text('Taking you back…'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  });
}
