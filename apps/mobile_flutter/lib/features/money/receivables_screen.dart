import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../data/money/money_repository.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// COD receivables, parcel by parcel.
///
/// One row per delivered parcel with what is still owed on it. This is where a
/// seller answers "which of my parcels has not been paid for?", which is the
/// question the whole product is built around (master spec section 1.1).
class ReceivablesScreen extends ConsumerWidget {
  const ReceivablesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(receivableListProvider);
    final controller = ref.read(receivableListProvider.notifier);

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
                          eyebrow: 'Delivered is not paid',
                          title: 'Receivables',
                          description:
                              'What each courier still owes you, parcel by '
                              'parcel.',
                        ),
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
                        label: 'Still owed',
                        selected: controller.status == null,
                        onChanged: (_) => controller.setStatus(null),
                      ),
                      FilterToggle(
                        label: 'Paid',
                        selected: controller.status == 'SETTLED',
                        onChanged: (selected) =>
                            controller.setStatus(selected ? 'SETTLED' : null),
                      ),
                      FilterToggle(
                        label: 'Amount wrong',
                        selected: controller.status == 'MISMATCHED',
                        onChanged: (selected) => controller.setStatus(
                          selected ? 'MISMATCHED' : null,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<Receivable>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.check_circle_outline,
                    emptyTitle: 'Nothing outstanding',
                    emptyMessage:
                        'Every delivered parcel has been paid for. Receivables '
                        'appear here as soon as a parcel is delivered.',
                    itemBuilder: (context, receivable) => ReceivableRow(
                      receivable: receivable,
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) =>
                              ReceivableDetailScreen(receivable: receivable),
                        ),
                      ),
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

/// One receivable row.
class ReceivableRow extends StatelessWidget {
  const ReceivableRow({required this.receivable, super.key, this.onTap});

  final Receivable receivable;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      onTap: onTap,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  receivable.orderNumber ?? 'Parcel',
                  style: EcomsbdType.bodyStrong,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              StatusChip(
                label: receivable.statusLabel,
                tone: _toneFor(receivable.status),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Row(
            children: <Widget>[
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      'STILL OWED',
                      style: EcomsbdType.eyebrow.copyWith(
                        color: EcomsbdColors.muted2,
                      ),
                    ),
                    MoneyText(
                      receivable.outstanding,
                      style: EcomsbdType.sectionTitle,
                    ),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(
                    receivable.ageDays == null
                        ? receivable.provider
                        : 'Waiting ${receivable.ageDays} day'
                              '${receivable.ageDays == 1 ? '' : 's'}',
                    style: EcomsbdType.caption.copyWith(
                      color: receivable.isWaitingTooLong
                          ? EcomsbdColors.red
                          : EcomsbdColors.muted,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'of ${receivable.collectible.format()}',
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  ),
                ],
              ),
            ],
          ),
          if (!receivable.deduction.isZero) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              '${receivable.deduction.format()} deducted by the courier',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  static Tone _toneFor(String status) => switch (status) {
    'SETTLED' => Tone.good,
    'ELIGIBLE' => Tone.warning,
    'PARTIALLY_SETTLED' => Tone.warning,
    'MISMATCHED' => Tone.bad,
    'DISPUTED' => Tone.bad,
    'WRITTEN_OFF' => Tone.neutral,
    _ => Tone.neutral,
  };
}

/// One receivable, with the ledger that explains it.
class ReceivableDetailScreen extends ConsumerStatefulWidget {
  const ReceivableDetailScreen({required this.receivable, super.key});

  final Receivable receivable;

  @override
  ConsumerState<ReceivableDetailScreen> createState() =>
      _ReceivableDetailScreenState();
}

class _ReceivableDetailScreenState
    extends ConsumerState<ReceivableDetailScreen> {
  bool _busy = false;

  Future<void> _correct() async {
    final result = await _CorrectionDialog.show(context);
    if (result == null) {
      return;
    }
    await _run(
      () => ref
          .read(moneyRepositoryProvider)
          .correct(
            widget.receivable.id,
            amountPaisa: result.amountPaisa,
            increasesBalance: result.increases,
            reason: result.reason,
          ),
    );
  }

  Future<void> _writeOff() async {
    final reason = await _ReasonDialog.show(
      context,
      title: 'Write this off?',
      body:
          'The money stops counting as owed, and the loss is recorded so you '
          'can see it later. This cannot be undone from here.',
      action: 'Write off',
    );
    if (reason == null) {
      return;
    }
    await _run(
      () => ref
          .read(moneyRepositoryProvider)
          .writeOff(widget.receivable.id, reason: reason),
    );
  }

  Future<void> _dispute() async {
    final reason = await _ReasonDialog.show(
      context,
      title: 'Raise this with the courier',
      body:
          'The money stays counted as owed. This just records that you have '
          'queried it.',
      action: 'Mark disputed',
    );
    if (reason == null) {
      return;
    }
    await _run(
      () => ref
          .read(moneyRepositoryProvider)
          .dispute(widget.receivable.id, reason: reason),
    );
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      ref.invalidate(receivableLedgerProvider(widget.receivable.id));
      ref.invalidate(moneySummaryProvider);
      await ref.read(receivableListProvider.notifier).refresh();
      if (mounted) {
        Navigator.of(context).pop();
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

  @override
  Widget build(BuildContext context) {
    final receivable = widget.receivable;
    final ledger = ref.watch(receivableLedgerProvider(receivable.id));

    return DetailScaffold(
      title: receivable.orderNumber ?? 'Receivable',
      subtitle: '${receivable.provider} · ${receivable.statusLabel}',
      children: <Widget>[
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                'STILL OWED',
                style: EcomsbdType.eyebrow.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
              MoneyText(receivable.outstanding, style: EcomsbdType.heroMoney),
              const SizedBox(height: EcomsbdSpacing.md),
              _Line(
                label: 'Collected by courier',
                amount: receivable.collectible,
              ),
              if (!receivable.adjustment.isZero)
                _Line(label: 'Your correction', amount: receivable.adjustment),
              _Line(label: 'Paid to you', amount: receivable.settled),
              _Line(label: 'Courier deductions', amount: receivable.deduction),
            ],
          ),
        ),
        if (receivable.statusReason != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
          GlassCard(
            child: Text(
              receivable.statusReason!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
        const SectionHeader(
          title: 'What happened',
          subtitle: 'Every money event behind this parcel',
        ),
        ledger.when(
          loading: () => SkeletonLoader.card(height: 120),
          error: (error, _) => Text(
            'Could not load the history.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          data: (entries) => Column(
            children: <Widget>[
              for (final entry in entries)
                Padding(
                  padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                  child: _LedgerRow(entry: entry),
                ),
            ],
          ),
        ),
        const SectionHeader(title: 'Fix this'),
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            OutlinedButton.icon(
              onPressed: _busy ? null : _correct,
              icon: const Icon(Icons.tune_rounded, size: 17),
              label: const Text('Correct the amount'),
              style: _buttonStyle(),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : _dispute,
              icon: const Icon(Icons.flag_outlined, size: 17),
              label: const Text('Raise with courier'),
              style: _buttonStyle(),
            ),
            OutlinedButton.icon(
              onPressed: _busy ? null : _writeOff,
              icon: const Icon(Icons.money_off_rounded, size: 17),
              label: const Text('Write off'),
              style: _buttonStyle(danger: true),
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        Text(
          'A correction records the change and your reason. It never quietly '
          'rewrites what was already settled.',
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        ),
      ],
    );
  }

  ButtonStyle _buttonStyle({bool danger = false}) => OutlinedButton.styleFrom(
    minimumSize: const Size(0, EcomsbdTouch.minTarget),
    shape: const StadiumBorder(),
    foregroundColor: danger ? EcomsbdColors.red : EcomsbdColors.ink,
    textStyle: EcomsbdType.label,
  );
}

class _Line extends StatelessWidget {
  const _Line({required this.label, required this.amount});

  final String label;
  final dynamic amount;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          MoneyText(amount, style: EcomsbdType.body),
        ],
      ),
    );
  }
}

class _LedgerRow extends StatelessWidget {
  const _LedgerRow({required this.entry});

  final LedgerEntry entry;

  @override
  Widget build(BuildContext context) {
    final tone = entry.isCredit ? Tone.good : Tone.bad;
    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      borderRadius: EcomsbdRadii.cardMedium,
      child: Row(
        children: <Widget>[
          Container(
            width: 34,
            height: 34,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: tone.surface,
              borderRadius: EcomsbdRadii.cardSmall,
            ),
            child: Icon(
              entry.isReversal
                  ? Icons.undo_rounded
                  : (entry.isCredit
                        ? Icons.arrow_downward_rounded
                        : Icons.arrow_upward_rounded),
              size: 16,
              color: tone.ink,
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(entry.eventLabel, style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  <String?>[
                    formatRelative(entry.occurredAt),
                    entry.reason,
                  ].whereType<String>().join(' · '),
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
          Text(
            '${entry.isCredit ? '+' : '-'}${entry.amount.format()}',
            style: EcomsbdType.bodyStrong.copyWith(color: tone.ink),
          ),
        ],
      ),
    );
  }
}

/// A correction: a change and a reason, never a new balance.
class _CorrectionResult {
  const _CorrectionResult({
    required this.amountPaisa,
    required this.increases,
    required this.reason,
  });

  final int amountPaisa;
  final bool increases;
  final String reason;
}

class _CorrectionDialog extends StatefulWidget {
  const _CorrectionDialog();

  static Future<_CorrectionResult?> show(BuildContext context) =>
      showDialog<_CorrectionResult>(
        context: context,
        builder: (_) => const _CorrectionDialog(),
      );

  @override
  State<_CorrectionDialog> createState() => _CorrectionDialogState();
}

class _CorrectionDialogState extends State<_CorrectionDialog> {
  final TextEditingController _amount = TextEditingController();
  final TextEditingController _reason = TextEditingController();
  bool _increases = false;

  @override
  void dispose() {
    _amount.dispose();
    _reason.dispose();
    super.dispose();
  }

  int? get _paisa {
    final text = _amount.text.trim();
    if (text.isEmpty) {
      return null;
    }
    final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
    if (match == null) {
      return null;
    }
    return int.parse(match.group(1)!) * 100 +
        int.parse((match.group(2) ?? '').padRight(2, '0'));
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Correct this amount'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'Say how much to change it by and why. What has already been '
              'settled is not touched.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            Row(
              children: <Widget>[
                Expanded(
                  child: FilterToggle(
                    label: 'Owe me less',
                    selected: !_increases,
                    onChanged: (_) => setState(() => _increases = false),
                  ),
                ),
                const SizedBox(width: EcomsbdSpacing.xs),
                Expanded(
                  child: FilterToggle(
                    label: 'Owe me more',
                    selected: _increases,
                    onChanged: (_) => setState(() => _increases = true),
                  ),
                ),
              ],
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: context.tr('field.amountTaka'),
              controller: _amount,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: 'Reason',
              controller: _reason,
              maxLines: 2,
              hint: 'Courier confirmed a discount at the door',
            ),
          ],
        ),
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: (_paisa ?? 0) > 0 && _reason.text.trim().length >= 5
              ? () => Navigator.of(context).pop(
                  _CorrectionResult(
                    amountPaisa: _paisa!,
                    increases: _increases,
                    reason: _reason.text.trim(),
                  ),
                )
              : null,
          child: const Text('Apply'),
        ),
      ],
    );
  }
}

class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog({
    required this.title,
    required this.body,
    required this.action,
  });

  final String title;
  final String body;
  final String action;

  static Future<String?> show(
    BuildContext context, {
    required String title,
    required String body,
    required String action,
  }) => showDialog<String>(
    context: context,
    builder: (_) => _ReasonDialog(title: title, body: body, action: action),
  );

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
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
          onPressed: _reason.text.trim().length >= 5
              ? () => Navigator.of(context).pop(_reason.text.trim())
              : null,
          child: Text(widget.action),
        ),
      ],
    );
  }
}
