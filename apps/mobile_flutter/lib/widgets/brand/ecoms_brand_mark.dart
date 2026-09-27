import 'package:flutter/material.dart';

import 'ecoms_animated_logo.dart';

/// The ecomsbd brand mark at a fixed size, for everyday UI such as the top bar
/// and the sign-in and onboarding headers.
///
/// Still by default. With [animate], it makes the splash screen's hop now and
/// then, resting [idleRest] between hops: alive in the header without
/// fidgeting, and idle (no frames scheduled) most of the time. The hop is
/// paint-only, so the box, and anything laid out around it, never moves.
/// Loading states use `EcomsLogoLoader` instead. All draw the same supplied
/// artwork ([EcomsAnimatedLogo.assetPath]) with no tint or effect.
///
/// [size] is the box the mark fills. The artwork file carries transparent
/// margin around the mark (the mark spans about 76% x 83% of its canvas), so
/// the canvas is drawn slightly larger than [size] and centred, letting the
/// mark itself fill the box. Only that transparent margin, and the top of a
/// hop, extend past the box; layout sees exactly [size] x [size].
class EcomsBrandMark extends StatelessWidget {
  const EcomsBrandMark({
    required this.size,
    super.key,
    this.animate = false,
    this.semanticLabel,
  });

  /// How long an animated mark stands still between hops.
  static const Duration idleRest = Duration(milliseconds: 2200);

  final double size;

  /// Whether the mark hops now and then. It stays still regardless while the
  /// system "remove animations" setting is on.
  final bool animate;

  /// Read by screen readers. Null, the default, treats the mark as decorative,
  /// which is right wherever the product name is written beside it.
  final String? semanticLabel;

  @override
  Widget build(BuildContext context) {
    final canvas = size * _canvasScale;
    final pixelRatio = MediaQuery.maybeDevicePixelRatioOf(context) ?? 1;
    return SizedBox.square(
      dimension: size,
      child: OverflowBox(
        minWidth: canvas,
        maxWidth: canvas,
        minHeight: canvas,
        maxHeight: canvas,
        child: animate
            ? EcomsAnimatedLogo(
                size: canvas,
                rest: idleRest,
                semanticLabel: semanticLabel,
              )
            : Image.asset(
                EcomsAnimatedLogo.assetPath,
                width: canvas,
                height: canvas,
                fit: BoxFit.contain,
                // Decode at display size rather than the 1416 px source.
                cacheWidth: (canvas * pixelRatio).ceil(),
                filterQuality: FilterQuality.medium,
                semanticLabel: semanticLabel,
                excludeFromSemantics: semanticLabel == null,
              ),
      ),
    );
  }
}

/// How much larger than the box the artwork canvas is drawn: enough for the
/// mark (about 83% of the canvas at its tallest) to fill the box without
/// spilling past it.
const double _canvasScale = 1.18;
