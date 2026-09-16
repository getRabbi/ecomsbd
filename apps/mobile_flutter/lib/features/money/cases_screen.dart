import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// The things that need a person.
///
/// Master spec section 16 requires a separate issue entity for each of its
/// cases, and this screen is why: a "delivered but unpaid" that exists only as
/// a filter on a list is a problem nobody is accountable for. Here each one has
/// a summary, an amount, and an outcome the seller records.
class CasesScreen extends ConsumerStatefulWidget {
  const CasesScreen({super.key});

  @override
  ConsumerState<CasesScreen> createState() => _CasesScreenState();
}

class _CasesScreenState extends ConsumerState<CasesScreen> {
  bool _busy = false;

  Future<void> _scan() async {
    setState(() => _busy = true);
    try {
      final opened = await ref.read(moneyRepositoryProvider).scanForCases();
      await ref.read(caseListProvider.notifier).refresh();
      ref.invalidate(moneySummaryProvider);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              opened == 0
                  ? 'Nothing new to worry about.'
                  : '$opened new thing${opened == 1 ? '' : 's'} to look at.',
            ),
          ),
        );
      }
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _close(ReconciliationCase item, {required bool dismiss}) async {
    final resolution = await _ResolutionDialog.show(
      context,
      dismiss: dismiss,
      summary: item.summary,
    );
    if (resolution == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(moneyRepositoryProvider)
          .updateCase(
            item.id,
            status: dismiss ? 'DISMISSED' : 'RESOLVED',
            resolution: resolution,
          );
      await ref.read(caseListProvider.notifier).refresh();
      ref.invalidate(moneySummaryProvider);
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(caseListProvider);
    final controller = ref.read(caseListProvider.notifier);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: controller.refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  EcomsbdLayout.pushedTopPadding,
                  EcomsbdSpacing.page,
                  EcomsbdSpacing.xxl,
                ),
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      IconButton(
                        onPressed: () => Navigator.of(context).maybePop(),
                        icon: const Icon(Icons.arrow_back_rounded),
                        tooltip: 'Back',
                      ),
                      const Expanded(
                        child: PageHeader(
                          eyebrow: 'Money that needs a person',
                          title: 'Needs you',
                          description:
                              'Money that did not arrive, arrived short, or '
                              'could not be placed.',
                        ),
                      ),
                      IconButton(
                        onPressed: _busy ? null : _scan,
                        icon: const Icon(Icons.refresh_rounded),
                        tooltip: 'Check again',
                      ),
                    ],
                  ),
                  if (state.isStale) ...<Widget>[
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                  ],
                  Wrap(
                    spacing: EcomsbdSpacing.xs,
                    runSpacing: EcomsbdSpacing.xs,
                    children: <Widget>[
                      FilterToggle(
                        label: 'Open',
                        selected: controller.status == 'OPEN',
                        onChanged: (selected) =>
                            controller.setStatus(selected ? 'OPEN' : null),
                      ),
                      FilterToggle(
                        label: 'Sorted',
                        selected: controller.status == 'RESOLVED',
                        onChanged: (selected) =>
                            controller.setStatus(selected ? 'RESOLVED' : null),
                      ),
                      FilterToggle(
                        label: 'All',
                        selected: controller.status == null,
                        onChanged: (_) => controller.setStatus(null),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<ReconciliationCase>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.check_circle_outline,
                    emptyTitle: 'Nothing needs you',
                    emptyMessage:
                        'Every parcel that was delivered has been paid for, and '
                        'every payment has been placed.',
                    itemBuilder: (context, item) => CaseCard(
                      item: item,
                      busy: _busy,
                      onResolve: () => _close(item, dismiss: false),
                      onDismiss: () => _close(item, dismiss: true),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// One case.
class CaseCard extends StatelessWidget {
  const CaseCard({
    required this.item,
    super.key,
    this.busy = false,
    this.onResolve,
    this.onDismiss,
  });

  final ReconciliationCase item;
  final bool busy;
  final VoidCallback? onResolve;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    final tone = item.isHighPriority ? Tone.bad : Tone.warning;

    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              // Flexible, not Spacer: a long kind label plus a lakh-scale
              // amount is wider than a 360dp row.
              Flexible(
                child: StatusChip(
                  label: item.kindLabel,
                  tone: item.isOpen ? tone : Tone.neutral,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              if (!item.amount.isZero)
                MoneyText(item.amount, style: EcomsbdType.bodyStrong),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          // The server's own wording, shown as-is so support and the seller
          // are reading the same sentence.
          Text(item.summary, style: EcomsbdType.body),
          const SizedBox(height: 4),
          Text(
            '${item.statusLabel} · opened ${formatRelative(item.openedAt)}',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
          if (item.resolution != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              item.resolution!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (item.isOpen && onResolve != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Row(
              children: <Widget>[
                Expanded(
                  child: OutlinedButton(
                    onPressed: busy ? null : onDismiss,
                    style: OutlinedButton.styleFrom(
                      minimumSize: const Size.fromHeight(
                        EcomsbdTouch.minTarget,
                      ),
                      shape: const StadiumBorder(),
                      foregroundColor: EcomsbdColors.muted,
                      textStyle: EcomsbdType.label,
                    ),
                    child: const Text('Not a problem'),
                  ),
                ),
                const SizedBox(width: EcomsbdSpacing.sm),
                Expanded(
                  child: FilledButton(
                    onPressed: busy ? null : onResolve,
                    style: FilledButton.styleFrom(
                      backgroundColor: EcomsbdColors.orange,
                      minimumSize: const Size.fromHeight(
                        EcomsbdTouch.minTarget,
                      ),
                      shape: const StadiumBorder(),
                      textStyle: EcomsbdType.label,
                    ),
                    child: const Text('Sorted'),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _ResolutionDialog extends StatefulWidget {
  const _ResolutionDialog({required this.dismiss, required this.summary});

  final bool dismiss;
  final String summary;

  static Future<String?> show(
    BuildContext context, {
    required bool dismiss,
    required String summary,
  }) => showDialog<String>(
    context: context,
    builder: (_) => _ResolutionDialog(dismiss: dismiss, summary: summary),
  );

  @override
  State<_ResolutionDialog> createState() => _ResolutionDialogState();
}

class _ResolutionDialogState extends State<_ResolutionDialog> {
  final TextEditingController _resolution = TextEditingController();

  @override
  void dispose() {
    _resolution.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.dismiss ? 'Not a problem?' : 'What happened?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            widget.summary,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Note',
            controller: _resolution,
            maxLines: 2,
            hint: widget.dismiss
                ? 'The courier had already paid it separately'
                : 'Courier paid the balance on the 12th',
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            // Closing a case with no explanation teaches nobody anything, and
            // the same problem comes back next month.
            'A note is needed so you know what happened if this comes up again.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _resolution.text.trim().isNotEmpty
              ? () => Navigator.of(context).pop(_resolution.text.trim())
              : null,
          child: Text(widget.dismiss ? 'Dismiss' : 'Mark sorted'),
        ),
      ],
    );
  }
}
