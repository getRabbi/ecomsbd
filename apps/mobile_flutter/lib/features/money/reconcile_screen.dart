import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// One payout, line by line, with what the engine decided.
///
/// The screen is deliberately built around the engine's *refusals*. Master spec
/// section 112 puts precision before recall, so most of what a seller does here
/// is confirm the things the engine would not settle on its own — and the
/// reason it refused is shown next to each one.
class ReconcileScreen extends ConsumerStatefulWidget {
  const ReconcileScreen({required this.payoutId, super.key});

  final String payoutId;

  @override
  ConsumerState<ReconcileScreen> createState() => _ReconcileScreenState();
}

class _ReconcileScreenState extends ConsumerState<ReconcileScreen> {
  bool _busy = false;
  ReconcileReport? _lastReport;

  Future<void> _reconcile({required bool shadow}) async {
    setState(() => _busy = true);
    try {
      final report = await ref
          .read(moneyRepositoryProvider)
          .reconcile(widget.payoutId, shadow: shadow);
      if (!mounted) {
        return;
      }
      setState(() => _lastReport = report);
      if (!shadow) {
        ref
          ..invalidate(payoutProvider(widget.payoutId))
          ..invalidate(moneySummaryProvider);
        await ref.read(receivableListProvider.notifier).refresh();
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

  Future<void> _match(PayoutLine line, MatchCandidate candidate) async {
    final reason = await _MatchReasonDialog.show(
      context,
      reference: candidate.merchantReference,
    );
    if (reason == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(moneyRepositoryProvider)
          .matchLine(
            line.id,
            receivableId: candidate.receivableId,
            reason: reason,
          );
      ref
        ..invalidate(payoutProvider(widget.payoutId))
        ..invalidate(moneySummaryProvider);
      await ref.read(receivableListProvider.notifier).refresh();
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

  Future<void> _unmatch(PayoutLine line) async {
    final reason = await _MatchReasonDialog.show(
      context,
      reference: null,
      title: 'Undo this match',
      body:
          'The payment goes back to unmatched and the parcel is owed again. '
          'Nothing is deleted — the history keeps both.',
      action: 'Undo',
    );
    if (reason == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(moneyRepositoryProvider)
          .unmatchLine(line.id, reason: reason);
      ref
        ..invalidate(payoutProvider(widget.payoutId))
        ..invalidate(moneySummaryProvider);
      await ref.read(receivableListProvider.notifier).refresh();
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
    final async = ref.watch(payoutProvider(widget.payoutId));

    return async.when(
      loading: () => DetailScaffold(
        title: 'Payout',
        children: <Widget>[SkeletonLoader.card(height: 200)],
      ),
      error: (error, _) => DetailScaffold(
        title: 'Payout',
        children: <Widget>[
          if (error is ApiError)
            ErrorStateCard(
              error: error,
              onRetry: () => ref.invalidate(payoutProvider(widget.payoutId)),
            )
          else
            EmptyState(
              icon: Icons.error_outline,
              title: 'Could not load',
              message: '$error',
            ),
        ],
      ),
      data: _body,
    );
  }

  Widget _body(Payout payout) {
    final report = _lastReport;

    return DetailScaffold(
      title: payout.total.format(),
      subtitle: '${payout.sourceLabel} · ${payout.statusLabel}',
      children: <Widget>[
        ResponsiveGrid(
          minTileWidth: 150,
          maxColumns: 2,
          spacing: EcomsbdSpacing.xs,
          children: <Widget>[
            MetricTile(
              label: 'Arrived',
              value: payout.total.formatCompact(),
              caption: payout.paidOn == null
                  ? formatRelative(payout.receivedAt)
                  : 'paid ${formatRelative(payout.paidOn)}',
            ),
            MetricTile(
              label: 'Tied to parcels',
              value: payout.applied.formatCompact(),
              caption: payout.isFullyExplained
                  ? 'all of it'
                  : '${payout.unexplained.format()} left',
              tone: payout.isFullyExplained ? Tone.good : Tone.warning,
            ),
          ],
        ),
        if (report != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
          _ReportCard(report: report),
        ],
        const SizedBox(height: EcomsbdSpacing.sm),
        Row(
          children: <Widget>[
            Expanded(
              child: OutlinedButton.icon(
                onPressed: _busy ? null : () => _reconcile(shadow: true),
                icon: const Icon(Icons.visibility_outlined, size: 17),
                label: const Text('Preview'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: FilledButton.icon(
                onPressed: _busy ? null : () => _reconcile(shadow: false),
                icon: const Icon(Icons.auto_awesome_outlined, size: 17),
                label: const Text('Match'),
                style: FilledButton.styleFrom(
                  backgroundColor: EcomsbdColors.orange,
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        Text(
          'Preview shows what would be matched without changing anything. '
          'Only lines with an exact courier reference are settled on their own.',
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        ),
        if (payout.adjustments.isNotEmpty) ...<Widget>[
          const SectionHeader(title: 'What the courier deducted'),
          GlassCard(
            child: Column(
              children: <Widget>[
                for (final adjustment in payout.adjustments)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 4),
                    child: Row(
                      children: <Widget>[
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            mainAxisSize: MainAxisSize.min,
                            children: <Widget>[
                              Text(
                                adjustment.typeLabel,
                                style: EcomsbdType.body.copyWith(
                                  color: adjustment.isUnknown
                                      ? EcomsbdColors.amber
                                      : null,
                                ),
                              ),
                              if (adjustment.isUnknown &&
                                  adjustment.rawText != null)
                                Text(
                                  // The courier's own words, kept so the seller
                                  // can ask about them.
                                  'Courier wrote: ${adjustment.rawText}',
                                  style: EcomsbdType.caption.copyWith(
                                    color: EcomsbdColors.muted,
                                  ),
                                ),
                            ],
                          ),
                        ),
                        MoneyText(
                          adjustment.amount,
                          style: EcomsbdType.bodyStrong,
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
        SectionHeader(
          title: 'Lines',
          subtitle: payout.linesNeedingAttention == 0
              ? 'All settled'
              : '${payout.linesNeedingAttention} need you',
        ),
        for (final line in payout.lines)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
            child: _LineCard(
              line: line,
              busy: _busy,
              onMatch: (candidate) => _match(line, candidate),
              onUnmatch: () => _unmatch(line),
            ),
          ),
      ],
    );
  }
}

class _ReportCard extends StatelessWidget {
  const _ReportCard({required this.report});

  final ReconcileReport report;

  @override
  Widget build(BuildContext context) {
    // Section 82's own summary format.
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                report.shadow
                    ? Icons.visibility_outlined
                    : Icons.check_circle_outline,
                size: 18,
                color: report.shadow ? EcomsbdColors.blue : EcomsbdColors.green,
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Text(
                  report.shadow ? 'Preview only' : 'Matched',
                  style: EcomsbdType.bodyStrong,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${report.exactMatches} exact · ${report.suggested} to check · '
            '${report.unresolved} unresolved',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (report.shadow)
            Text(
              'Nothing was changed.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.blue),
            )
          else if (!report.applied.isZero)
            Text(
              '${report.applied.format()} settled.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.green),
            ),
        ],
      ),
    );
  }
}

class _LineCard extends StatelessWidget {
  const _LineCard({
    required this.line,
    required this.busy,
    required this.onMatch,
    required this.onUnmatch,
  });

  final PayoutLine line;
  final bool busy;
  final ValueChanged<MatchCandidate> onMatch;
  final VoidCallback onUnmatch;

  @override
  Widget build(BuildContext context) {
    final eligible = line.candidates.where((c) => c.isEligible).toList();

    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  line.reference,
                  style: EcomsbdType.bodyStrong,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              MoneyText(line.amount, style: EcomsbdType.bodyStrong),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Row(
            children: <Widget>[
              StatusChip(label: line.statusLabel, tone: _toneFor(line.status)),
              if (line.isApplied) ...<Widget>[
                const Spacer(),
                TextButton(
                  onPressed: busy ? null : onUnmatch,
                  style: TextButton.styleFrom(
                    foregroundColor: EcomsbdColors.muted,
                    minimumSize: const Size(0, EcomsbdTouch.minTarget),
                    textStyle: EcomsbdType.chip,
                  ),
                  child: const Text('Undo'),
                ),
              ],
            ],
          ),
          if (line.errors.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              line.errors.join(' · '),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          ],
          if (line.matchReason != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              line.matchReason!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (line.needsAttention && eligible.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              eligible.length == 1
                  ? 'One parcel could be this'
                  : '${eligible.length} parcels could be this',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
            for (final candidate in eligible.take(3))
              Padding(
                padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xxs),
                child: _CandidateRow(
                  candidate: candidate,
                  onMatch: busy ? null : () => onMatch(candidate),
                ),
              ),
          ] else if (line.needsAttention) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              'Nothing in your shop matches this line.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  static Tone _toneFor(String status) => switch (status) {
    'MATCHED' => Tone.good,
    'MANUAL_MATCHED' => Tone.good,
    'SUGGESTED' => Tone.warning,
    'DUPLICATE' => Tone.bad,
    'UNMAPPABLE' => Tone.bad,
    'REVERSED' => Tone.neutral,
    _ => Tone.neutral,
  };
}

class _CandidateRow extends StatelessWidget {
  const _CandidateRow({required this.candidate, this.onMatch});

  final MatchCandidate candidate;
  final VoidCallback? onMatch;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: const BoxDecoration(
        color: EcomsbdColors.miniTile,
        borderRadius: EcomsbdRadii.cardSmall,
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  candidate.merchantReference,
                  style: EcomsbdType.body,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 2),
                Text(
                  // Why the engine thought so, in the seller's words.
                  <String>[
                    candidate.outstanding.format(),
                    ...candidate.signalLabels,
                  ].join(' · '),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          TextButton(
            onPressed: onMatch,
            style: TextButton.styleFrom(
              foregroundColor: EcomsbdColors.orange,
              minimumSize: const Size(0, EcomsbdTouch.minTarget),
              textStyle: EcomsbdType.chip,
            ),
            child: const Text('This one'),
          ),
        ],
      ),
    );
  }
}

class _MatchReasonDialog extends StatefulWidget {
  const _MatchReasonDialog({
    required this.title,
    required this.body,
    required this.action,
  });

  final String title;
  final String body;
  final String action;

  static Future<String?> show(
    BuildContext context, {
    required String? reference,
    String? title,
    String? body,
    String? action,
  }) => showDialog<String>(
    context: context,
    builder: (_) => _MatchReasonDialog(
      title: title ?? 'Match this payment',
      body:
          body ??
          'Say why this payment is for ${reference ?? 'this parcel'}. It is '
              'stored with your name, so the decision can be traced later.',
      action: action ?? 'Match',
    ),
  );

  @override
  State<_MatchReasonDialog> createState() => _MatchReasonDialogState();
}

class _MatchReasonDialogState extends State<_MatchReasonDialog> {
  final TextEditingController _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: Text(widget.title),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            widget.body,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Reason',
            controller: _reason,
            maxLines: 2,
            hint: 'Courier confirmed by phone',
            onChanged: (_) => setState(() {}),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: _reason.text.trim().length >= 3
              ? () => Navigator.of(context).pop(_reason.text.trim())
              : null,
          child: Text(widget.action),
        ),
      ],
    );
  }
}
