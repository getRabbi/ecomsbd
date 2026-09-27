import 'dart:ui' show SemanticsFlag;

import 'package:ecomsbd/design/components/pills.dart';
import 'package:ecomsbd/features/splash/splash_screen.dart';
import 'package:ecomsbd/widgets/brand/ecoms_animated_logo.dart';
import 'package:ecomsbd/widgets/brand/ecoms_animated_wordmark.dart';
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
        'assets/icon/logo_mark_transparent.png',
      );
      // 110 dp at the reference 3x density, not the 1416 px source.
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

    testWidgets('rests between hops without scheduling frames', (tester) async {
      const rest = Duration(seconds: 2);
      await pumpAtSize(tester, _centred(const EcomsAnimatedLogo(rest: rest)));
      // The rest comes first: no hop the moment the screen appears.
      expect(tester.hasRunningAnimations, isFalse);

      for (var hop = 0; hop < 2; hop++) {
        await tester.pump(rest);
        expect(tester.hasRunningAnimations, isTrue);
        // A controller finishes on the first frame after its duration.
        await tester.pump(EcomsAnimatedLogo.period);
        await tester.pump(const Duration(milliseconds: 16));
        expect(tester.hasRunningAnimations, isFalse);
      }
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
      // Decoded near display size at 3x, not the 1416 px source.
      expect(image.width, inInclusiveRange(90, 120));
    });

    testWidgets('never animates', (tester) async {
      await pumpAtSize(tester, _centred(const EcomsBrandMark(size: 30)));
      await tester.pump(const Duration(seconds: 2));
      expect(tester.hasRunningAnimations, isFalse);
      expect(find.byType(EcomsAnimatedLogo), findsNothing);
    });

    testWidgets('an animated mark hops now and then inside a fixed box', (
      tester,
    ) async {
      await pumpAtSize(
        tester,
        _centred(const EcomsBrandMark(size: 30, animate: true)),
      );
      expect(find.byType(EcomsAnimatedLogo), findsOneWidget);
      final box = tester.getRect(find.byType(EcomsBrandMark));
      expect(box.size, const Size(30, 30));
      expect(tester.hasRunningAnimations, isFalse);

      await tester.pump(EcomsBrandMark.idleRest);
      expect(tester.hasRunningAnimations, isTrue);
      // Mid-hop: the mark moves, its box does not.
      await tester.pump(const Duration(milliseconds: 500));
      expect(tester.getRect(find.byType(EcomsBrandMark)), box);
      await tester.pumpAndSettle();
      expect(tester.getRect(find.byType(EcomsBrandMark)), box);
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
    testWidgets('shows the animated brand mark, not a placeholder letter', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const BrandPill()));
      expect(find.byType(EcomsBrandMark), findsOneWidget);
      expect(find.byType(EcomsAnimatedLogo), findsOneWidget);
      expect(find.text('ecomsbd'), findsOneWidget);
      expect(find.text('e'), findsNothing);
    });

    testWidgets('keeps the brand mark on section tabs', (tester) async {
      await pumpAtSize(tester, _centred(const BrandPill(label: 'Orders')));
      expect(find.byType(EcomsBrandMark), findsOneWidget);
      expect(find.text('Orders'), findsOneWidget);
      expect(find.text('o'), findsNothing);
      expect(tester.takeException(), isNull);
    });

    testWidgets('keeps its size and layout while the mark hops', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const BrandPill()));
      final pill = tester.getRect(find.byType(BrandPill));
      final label = tester.getRect(find.text('ecomsbd'));

      await tester.pump(EcomsBrandMark.idleRest);
      for (var i = 0; i < 7; i++) {
        await tester.pump(const Duration(milliseconds: 200));
        expect(tester.getRect(find.byType(BrandPill)), pill);
        expect(tester.getRect(find.text('ecomsbd')), label);
      }
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

  group('EcomsAnimatedWordmark', () {
    testWidgets('rises into place, then keeps a gentle wave going', (
      tester,
    ) async {
      await pumpAtSize(tester, _centred(const EcomsAnimatedWordmark()));
      expect(tester.hasRunningAnimations, isTrue);
      await tester.pump(EcomsAnimatedWordmark.entrance);
      await tester.pump(EcomsAnimatedWordmark.wavePeriod * 2);
      expect(tester.hasRunningAnimations, isTrue);
      expect(tester.takeException(), isNull);
    });

    testWidgets('is read once as the product name', (tester) async {
      final handle = tester.ensureSemantics();
      await pumpAtSize(tester, _centred(const EcomsAnimatedWordmark()));
      expect(find.semantics.byLabel('ecomsbd'), findsOne);
      handle.dispose();
    });

    testWidgets('stands still, fully shown, when animations are off', (
      tester,
    ) async {
      tester.platformDispatcher.accessibilityFeaturesTestValue =
          const FakeAccessibilityFeatures(disableAnimations: true);
      addTearDown(
        tester.platformDispatcher.clearAccessibilityFeaturesTestValue,
      );

      await pumpAtSize(tester, _centred(const EcomsAnimatedWordmark()));
      expect(tester.hasRunningAnimations, isFalse);
      for (final opacity in tester.widgetList<Opacity>(
        find.descendant(
          of: find.byType(EcomsAnimatedWordmark),
          matching: find.byType(Opacity),
        ),
      )) {
        expect(opacity.opacity, 1);
      }
    });
  });

  group('SplashScreen', () {
    testWidgets('shows the animated brand mark over the wordmark', (
      tester,
    ) async {
      await pumpAtSize(tester, const SplashScreen());
      expect(find.byType(EcomsLogoLoader), findsOneWidget);
      expect(find.byType(EcomsAnimatedWordmark), findsOneWidget);
      expect(
        tester.getRect(find.byType(EcomsAnimatedWordmark)).top,
        greaterThanOrEqualTo(
          tester.getRect(find.byType(EcomsLogoLoader)).bottom,
        ),
      );
      // Through the entrance and a few waves, at the smallest phone.
      await tester.pump(const Duration(seconds: 4));
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
