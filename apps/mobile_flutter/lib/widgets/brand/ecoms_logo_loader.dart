import 'package:flutter/material.dart';

import 'ecoms_animated_logo.dart';

/// The branded loading indicator: the brand mark hopping on a loop.
///
/// For full-screen, brand-level waits such as app start. Lists and cards keep
/// their skeletons (master spec section 52: "skeletons, not spinner forever").
class EcomsLogoLoader extends StatelessWidget {
  const EcomsLogoLoader({
    super.key,
    this.size = 96,
    this.semanticLabel = 'Loading',
  });

  /// Side of the mark's square canvas; see [EcomsAnimatedLogo.size].
  final double size;

  /// Announced by screen readers in place of the brand name.
  final String semanticLabel;

  @override
  Widget build(BuildContext context) {
    return EcomsAnimatedLogo(size: size, semanticLabel: semanticLabel);
  }
}
