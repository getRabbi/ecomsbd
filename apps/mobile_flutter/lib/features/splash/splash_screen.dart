import 'package:flutter/material.dart';

import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../widgets/brand/ecoms_animated_wordmark.dart';
import '../../widgets/brand/ecoms_logo_loader.dart';

/// Shown while the stored session is restored and verified.
///
/// The native launch window stays static; this is the first Flutter frame, and
/// the brand mark hops here, over the `ecomsbd` wordmark, until the router
/// moves on. Master spec section 53 asks for a fast cold start on low-end
/// devices, so the motion is kept cheap: transforms and opacity only, each on
/// its own layer, with the mark decoded at display size. Both stand still when
/// the system has animations turned off.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key, this.message});

  final String? message;

  @override
  Widget build(BuildContext context) {
    return EcomsbdScaffold(
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const EcomsLogoLoader(size: 110),
            // The mark's canvas already leaves ~9 dp under the bag.
            const SizedBox(height: EcomsbdSpacing.xs),
            const EcomsAnimatedWordmark(),
            if (message != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.md),
              Text(
                message!,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
