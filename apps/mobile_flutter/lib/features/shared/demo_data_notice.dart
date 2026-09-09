import 'package:flutter/material.dart';

import '../../design/glass.dart';
import '../../design/tokens.dart';

/// Marks a screen that is rendering fixtures rather than the seller's data.
///
/// Every screen wired to `lib/demo/` shows this. Without it a screenshot of the
/// dashboard is indistinguishable from real seller money, which is exactly the
/// confusion master spec section 135 exists to prevent ("a beautiful screen
/// backed by dummy data is not done").
///
/// This widget is deleted alongside the fixtures as each screen is wired to its
/// real endpoint.
class DemoDataNotice extends StatelessWidget {
  const DemoDataNotice({super.key, this.detail});

  /// Which endpoint will replace the fixtures.
  final String? detail;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: EcomsbdColors.blueSoft,
      borderColor: EcomsbdColors.blue.withValues(alpha: 0.2),
      borderRadius: EcomsbdRadii.cardMedium,
      shadows: const <BoxShadow>[],
      padding: const EdgeInsets.symmetric(
        horizontal: EcomsbdSpacing.md,
        vertical: EcomsbdSpacing.sm,
      ),
      child: Row(
        children: <Widget>[
          const Icon(
            Icons.science_outlined,
            size: 17,
            color: EcomsbdColors.blue,
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              detail ??
                  'Demo data — layout preview. Real figures arrive when the '
                      'analytics endpoints ship.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.blue),
            ),
          ),
        ],
      ),
    );
  }
}
