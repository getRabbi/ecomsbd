import 'package:flutter/material.dart';

import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Read where no `BuildContext` exists, so the active locale is resolved
/// directly -- the same approach `formatRelative` and `order_status.dart` use.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Seller-facing wording for a duplicate signal.
String duplicateReasonLabel(String reason) => switch (reason) {
  'SAME_PHONE' => _t('dup.sameNumber'),
  'SAME_CUSTOMER' => _t('dup.sameCustomer'),
  'IDENTICAL_AMOUNT' => _t('dup.sameAmount'),
  'SIMILAR_AMOUNT' => _t('dup.similarAmount'),
  'SIMILAR_ITEMS' => _t('dup.sameItems'),
  _ => reason,
};

/// Warns that a similar order was placed recently.
///
/// Advisory, and it says so. Master spec section 9: duplicate detection warns,
/// it never blocks — a customer ordering the same thing twice in a day is a
/// real workflow, and the seller is the only one who can tell the difference
/// between that and a double-tap.
class DuplicateWarningSheet extends StatelessWidget {
  const DuplicateWarningSheet({required this.check, super.key});

  final DuplicateCheck check;

  /// Returns true when the seller chose to continue.
  static Future<bool> show(BuildContext context, DuplicateCheck check) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => DuplicateWarningSheet(check: check),
    );
    return result ?? false;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: EcomsbdColors.backgroundLight,
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(EcomsbdRadii.lg),
        ),
      ),
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.lg,
        EcomsbdSpacing.md,
        EcomsbdSpacing.lg,
        EcomsbdSpacing.xl,
      ),
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Center(
              child: Container(
                width: 40,
                height: 4,
                decoration: BoxDecoration(
                  color: EcomsbdColors.trackLight,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            Row(
              children: <Widget>[
                Icon(
                  Icons.copy_all_outlined,
                  size: 20,
                  color: EcomsbdColors.amber,
                ),
                SizedBox(width: EcomsbdSpacing.sm),
                Expanded(
                  child: Text(
                    context.tr('dup.title'),
                    style: EcomsbdType.sectionTitle,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              check.message.isNotEmpty ? check.message : context.tr('dup.body'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            for (final candidate in check.candidates)
              Padding(
                padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                child: _CandidateCard(candidate: candidate),
              ),
            const SizedBox(height: EcomsbdSpacing.md),
            Text(
              context.tr('dup.note'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: () => Navigator.of(context).pop(false),
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(
                        EcomsbdTouch.minTarget,
                      ),
                      shape: const StadiumBorder(),
                      textStyle: EcomsbdType.label,
                    ),
                    child: Text(context.tr('dup.letMeCheck')),
                  ),
                ),
                const SizedBox(width: EcomsbdSpacing.sm),
                Expanded(
                  child: FilledButton(
                    onPressed: () => Navigator.of(context).pop(true),
                    style: FilledButton.styleFrom(
                      backgroundColor: EcomsbdColors.orange,
                      minimumSize: const Size.fromHeight(
                        EcomsbdTouch.minTarget,
                      ),
                      shape: const StadiumBorder(),
                      textStyle: EcomsbdType.label,
                    ),
                    child: Text(context.tr('dup.keep')),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _CandidateCard extends StatelessWidget {
  const _CandidateCard({required this.candidate});

  final DuplicateCandidate candidate;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: EcomsbdRadii.cardMedium,
        border: Border.all(color: EcomsbdColors.stroke),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  candidate.orderNumber,
                  style: EcomsbdType.bodyStrong,
                ),
              ),
              Text(candidate.codAmount.format(), style: EcomsbdType.bodyStrong),
            ],
          ),
          const SizedBox(height: 2),
          Text(
            '${candidate.whenLabel} · ${candidate.status}',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Wrap(
            spacing: EcomsbdSpacing.xxs,
            runSpacing: EcomsbdSpacing.xxs,
            children: <Widget>[
              for (final reason in candidate.reasons)
                StatusChip(
                  label: duplicateReasonLabel(reason),
                  tone: candidate.isStrong ? Tone.warning : Tone.neutral,
                  showIcon: false,
                ),
            ],
          ),
        ],
      ),
    );
  }
}
