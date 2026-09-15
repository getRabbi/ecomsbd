import 'package:flutter/material.dart';

import 'ecoms_animated_logo.dart';

/// The ecomsbd brand mark, standing still.
///
/// For everyday UI such as the top bar and the sign-in and onboarding headers,
/// where the brand should be present but not moving. Loading states use
/// `EcomsLogoLoader` instead. Both draw the same supplied artwork
/// ([EcomsAnimatedLogo.assetPath]) with no tint, shadow or effect.
///
/// [size] is the box the mark fills. The artwork file carries transparent
/// margin around the mark (the mark spans about 74% x 83% of its canvas), so
/// the canvas is drawn slightly larger than [size] and centred, letting the
/// mark itself fill the box. Only that transparent margin extends past the
/// box; layout sees exactly [size] x [size].
class EcomsBrandMark extends StatelessWidget {
  const EcomsBrandMark({required this.size, super.key, this.semanticLabel});

  final double size;

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
        child: Image.asset(
          EcomsAnimatedLogo.assetPath,
          width: canvas,
          height: canvas,
          fit: BoxFit.contain,
          // Decode at display size rather than the 1254 px source.
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
