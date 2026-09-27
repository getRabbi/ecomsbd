import 'package:flutter/material.dart';

import '../../l10n/app_strings.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';

class FreeLaunchNotice extends StatelessWidget {
  const FreeLaunchNotice({super.key});

  @override
  Widget build(BuildContext context) => GlassCard(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(context.tr('launch.fullAccess'), style: EcomsbdType.sectionTitle),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(context.tr('launch.message')),
      ],
    ),
  );
}
