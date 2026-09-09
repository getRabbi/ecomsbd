import 'package:flutter/material.dart';

import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';

/// Shown while the stored session is restored and verified.
///
/// Deliberately quiet: master spec section 53 asks for a fast cold start on
/// low-end devices, and an animated splash competes with the work that actually
/// gates the first screen.
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
            const BrandPill(showChevron: false),
            const SizedBox(height: EcomsbdSpacing.xl),
            const SizedBox(
              width: 22,
              height: 22,
              child: CircularProgressIndicator(
                strokeWidth: 2.4,
                color: EcomsbdColors.orange,
              ),
            ),
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
