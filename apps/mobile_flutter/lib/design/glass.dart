import 'dart:ui';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'tokens.dart';

/// How much visual effect this device can afford.
///
/// `BackdropFilter` is the single most expensive widget in this design: each
/// one forces a saveLayer and re-blurs everything behind it, every frame. On a
/// mid-range Bangladeshi Android device a screen with a dozen frosted cards
/// drops frames badly, and master spec section 53 requires the app to stay
/// usable on exactly those devices.
///
/// [EffectsMode.reduced] therefore keeps the *appearance* — same colours, same
/// borders, same shadows — and replaces the live blur with a slightly more
/// opaque fill. Side by side the difference is barely visible; in a frame
/// profile it is the difference between smooth and stuttering.
enum EffectsMode {
  /// Live `BackdropFilter` blur.
  full,

  /// Performant semi-transparent fallback.
  reduced,
}

/// The active effects mode.
///
/// Defaults to [EffectsMode.full] and is lowered at startup by the device-tier
/// probe (see `core/device/device_tier.dart`). Overridable so the setting can
/// be exposed to the seller and pinned in widget tests.
final effectsModeProvider = StateProvider<EffectsMode>(
  (ref) => EffectsMode.full,
);

/// A frosted surface: the base of every card, pill and sheet in the app.
///
/// Never construct a blurred container directly — routing all of them through
/// this widget is what makes the low-end fallback a single switch rather than a
/// sweep through every screen.
class GlassSurface extends ConsumerWidget {
  const GlassSurface({
    required this.child,
    super.key,
    this.fill = EcomsbdColors.glass,
    this.borderRadius = EcomsbdRadii.cardLarge,
    this.borderColor = EcomsbdColors.whiteStroke,
    this.borderWidth = 1,
    this.shadows = EcomsbdShadows.soft,
    this.blurSigma = 20,
    this.saturation = 1.55,
    this.padding,
    this.margin,
    this.clipBehavior = Clip.antiAlias,
  });

  final Widget child;
  final Color fill;
  final BorderRadius borderRadius;
  final Color? borderColor;
  final double borderWidth;
  final List<BoxShadow> shadows;
  final double blurSigma;

  /// Kept for parity with the prototype's `saturate(155%)`. Applied only in
  /// [EffectsMode.full]; the reduced path compensates with opacity instead.
  final double saturation;

  final EdgeInsetsGeometry? padding;
  final EdgeInsetsGeometry? margin;
  final Clip clipBehavior;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(effectsModeProvider);
    final blurred = mode == EffectsMode.full;

    // Without a live blur the surface has nothing behind it to pick up, so it
    // is made a little more opaque to keep the same perceived density.
    final effectiveFill = blurred ? fill : _thicken(fill);

    Widget surface = DecoratedBox(
      decoration: BoxDecoration(
        color: effectiveFill,
        borderRadius: borderRadius,
        border: borderColor == null
            ? null
            : Border.all(color: borderColor!, width: borderWidth),
      ),
      child: padding == null ? child : Padding(padding: padding!, child: child),
    );

    if (blurred) {
      surface = BackdropFilter(
        filter: ImageFilter.blur(sigmaX: blurSigma, sigmaY: blurSigma),
        child: surface,
      );
    }

    return Container(
      margin: margin,
      decoration: BoxDecoration(borderRadius: borderRadius, boxShadow: shadows),
      child: ClipRRect(
        borderRadius: borderRadius,
        clipBehavior: clipBehavior,
        child: surface,
      ),
    );
  }

  /// Raise alpha toward opaque without changing the hue.
  static Color _thicken(Color base) {
    final alpha = base.a;
    if (alpha >= 0.97) {
      return base;
    }
    return base.withValues(alpha: (alpha + (1 - alpha) * 0.55).clamp(0.0, 1.0));
  }
}

/// The page background: the prototype's two radial washes over a vertical
/// gradient. Painted once per screen, behind everything else.
class EcomsbdBackground extends StatelessWidget {
  const EcomsbdBackground({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(
        gradient: LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: <Color>[
            EcomsbdColors.gradientTop,
            EcomsbdColors.gradientMid,
            EcomsbdColors.gradientBottom,
          ],
          stops: <double>[0.0, 0.62, 1.0],
        ),
      ),
      child: Stack(
        children: <Widget>[
          const Positioned(
            left: -80,
            top: -120,
            width: 320,
            height: 320,
            child: _Wash(color: EcomsbdColors.washOrange),
          ),
          const Positioned(
            right: -90,
            top: -100,
            width: 300,
            height: 300,
            child: _Wash(color: EcomsbdColors.washBlue),
          ),
          Positioned.fill(child: child),
        ],
      ),
    );
  }
}

class _Wash extends StatelessWidget {
  const _Wash({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: DecoratedBox(
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: RadialGradient(
            colors: <Color>[color, color.withValues(alpha: 0)],
          ),
        ),
      ),
    );
  }
}

/// Debug helper: force an effects mode in tests and screenshots.
@visibleForTesting
Override overrideEffectsMode(EffectsMode mode) =>
    effectsModeProvider.overrideWith((ref) => mode);
