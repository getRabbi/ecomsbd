import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/money/models.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart' as couriers;
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';
import 'reconcile_screen.dart';

/// Money that arrived, and where it came from.
class PayoutsScreen extends ConsumerStatefulWidget {
  const PayoutsScreen({super.key, this.pickFile = pickStatementFile});

  /// How a statement is chosen. Overridden in tests.
  final Future<PickedStatement?> Function() pickFile;

  @override
  ConsumerState<PayoutsScreen> createState() => _PayoutsScreenState();
}

/// A statement file the seller chose.
class PickedStatement {
  const PickedStatement({required this.name, required this.bytes});

  final String name;
  final List<int> bytes;
}

Future<PickedStatement?> pickStatementFile() async {
  final picked = await FilePicker.pickFiles(
    type: FileType.custom,
    allowedExtensions: <String>['csv', 'txt'],
    withData: true,
  );
  final file = picked?.files.singleOrNull;
  final bytes = file?.bytes;
  if (file == null || bytes == null) {
    return null;
  }
  return PickedStatement(name: file.name, bytes: bytes);
}

class _PayoutsScreenState extends ConsumerState<PayoutsScreen> {
  bool _busy = false;

  Future<void> _import() async {
    final file = await widget.pickFile();
    if (file == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      final repository = ref.read(moneyRepositoryProvider);

      // Read it first and show the seller what is in it. Nothing is created
      // until they have seen the rows, including the ones we could not read.
      final preview = await repository.previewStatement(
        bytes: file.bytes,
        filename: file.name,
      );
      if (!mounted) {
        return;
      }
      final confirmed = await _StatementPreviewSheet.show(context, preview);
      if (!confirmed || !mounted) {
        return;
      }

      final payout = await repository.importStatement(
        bytes: file.bytes,
        filename: file.name,
        provider: 'manual',
      );
      await ref.read(payoutListProvider.notifier).refresh();
      ref.invalidate(moneySummaryProvider);
      if (mounted) {
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => ReconcileScreen(payoutId: payout.id),
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

  Future<void> _recordManual() async {
    final result = await _ManualPayoutDialog.show(context);
    if (result == null) {
      return;
    }
    setState(() => _busy = true);
    try {
      final payout = await ref
          .read(moneyRepositoryProvider)
          .recordManualPayout(
            totalPaisa: result.amountPaisa,
            note: result.note,
          );
      await ref.read(payoutListProvider.notifier).refresh();
      ref.invalidate(moneySummaryProvider);
      if (mounted) {
        await Navigator.of(context).push(
          MaterialPageRoute<void>(
            builder: (_) => ReconcileScreen(payoutId: payout.id),
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

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(payoutListProvider);
    final controller = ref.read(payoutListProvider.notifier);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              onRefresh: controller.refresh,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(
                  EcomsbdSpacing.page,
                  0,
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
                          eyebrow: 'Statement → matched → settled',
                          title: 'Payouts',
                          description:
                              'What the courier says it sent, and what we could '
                              'tie to a parcel.',
                        ),
                      ),
                    ],
                  ),
                  ResponsiveGrid(
                    minTileWidth: 150,
                    maxColumns: 2,
                    spacing: EcomsbdSpacing.xs,
                    children: <Widget>[
                      QuickActionTile(
                        icon: Icons.upload_file_rounded,
                        title: 'Upload statement',
                        subtitle: 'CSV from the courier',
                        onTap: _busy ? null : _import,
                        enabled: !_busy,
                      ),
                      QuickActionTile(
                        icon: Icons.edit_note_rounded,
                        title: 'Enter by hand',
                        subtitle: 'A lump sum that arrived',
                        onTap: _busy ? null : _recordManual,
                        enabled: !_busy,
                      ),
                    ],
                  ),
                  if (state.isStale) ...<Widget>[
                    const SizedBox(height: EcomsbdSpacing.sm),
                    StaleDataNotice(
                      fetchedAt: state.fetchedAt,
                      onRetry: controller.refresh,
                    ),
                  ],
                  const _SteadfastPaymentsSection(),
                  const SectionHeader(title: 'Received'),
                  PagedListBody<Payout>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.account_balance_wallet_outlined,
                    emptyTitle: 'No payouts yet',
                    emptyMessage:
                        'Upload a courier statement, or enter what arrived by '
                        'hand. Nothing is matched until you look at it.',
                    itemBuilder: (context, payout) => PayoutRow(
                      payout: payout,
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => ReconcileScreen(payoutId: payout.id),
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

/// One payout row.
class PayoutRow extends StatelessWidget {
  const PayoutRow({required this.payout, super.key, this.onTap});

  final Payout payout;
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
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    MoneyText(payout.total, style: EcomsbdType.sectionTitle),
                    const SizedBox(height: 2),
                    Text(
                      '${payout.sourceLabel} · '
                      '${formatRelative(payout.receivedAt)}',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              StatusChip(
                label: payout.statusLabel,
                tone: payout.isFullyExplained ? Tone.good : Tone.warning,
              ),
            ],
          ),
          if (!payout.isFullyExplained) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              // The gap between what arrived and what has been explained,
              // stated rather than hidden.
              '${payout.unexplained.format()} not yet tied to a parcel',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
            ),
          ],
        ],
      ),
    );
  }
}

/// What a statement contains, before anything is created.
class _StatementPreviewSheet extends StatelessWidget {
  const _StatementPreviewSheet({required this.preview});

  final StatementPreview preview;

  static Future<bool> show(
    BuildContext context,
    StatementPreview preview,
  ) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _StatementPreviewSheet(preview: preview),
    );
    return result ?? false;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.85,
      ),
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
          const Text('Before we save this', style: EcomsbdType.sectionTitle),
          const SizedBox(height: 3),
          Text(
            '${preview.rowCount} row${preview.rowCount == 1 ? '' : 's'}, '
            '${preview.total.format()} in total. Nothing has been created yet.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          if (preview.hasProblems)
            ProviderHealthBanner(
              provider:
                  '${preview.invalidRowCount} row'
                  '${preview.invalidRowCount == 1 ? '' : 's'} we could not read',
              detail:
                  'They are imported with the text the file contained, so you '
                  'can see what went wrong rather than losing the money.',
              tone: Tone.warning,
            ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Flexible(
            child: ListView(
              shrinkWrap: true,
              children: <Widget>[
                for (final row in preview.rows.take(50))
                  Padding(
                    padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xxs),
                    child: _PreviewRow(row: row),
                  ),
                if (preview.rows.length > 50)
                  Padding(
                    padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
                    child: Text(
                      '${preview.rows.length - 50} more not shown.',
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          Row(
            children: <Widget>[
              Expanded(
                child: OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(false),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                    shape: const StadiumBorder(),
                    textStyle: EcomsbdType.label,
                  ),
                  child: const Text('Cancel'),
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: FilledButton(
                  onPressed: () => Navigator.of(context).pop(true),
                  style: FilledButton.styleFrom(
                    backgroundColor: EcomsbdColors.orange,
                    minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                    shape: const StadiumBorder(),
                    textStyle: EcomsbdType.label,
                  ),
                  child: const Text('Import it'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _PreviewRow extends StatelessWidget {
  const _PreviewRow({required this.row});

  final StatementRow row;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(
        horizontal: EcomsbdSpacing.md,
        vertical: EcomsbdSpacing.sm,
      ),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: EcomsbdRadii.cardSmall,
        border: Border.all(
          color: row.isValid ? EcomsbdColors.stroke : EcomsbdColors.redSoft,
        ),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  row.reference,
                  style: EcomsbdType.body,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                if (!row.isValid)
                  Text(
                    row.errors.join(' · '),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.red,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          Text(
            // No amount at all rather than a fabricated zero.
            row.amount?.format() ?? '—',
            style: EcomsbdType.bodyStrong.copyWith(
              color: row.isValid ? null : EcomsbdColors.red,
            ),
          ),
        ],
      ),
    );
  }
}

class _ManualPayoutResult {
  const _ManualPayoutResult({required this.amountPaisa, this.note});

  final int amountPaisa;
  final String? note;
}

class _ManualPayoutDialog extends StatefulWidget {
  const _ManualPayoutDialog();

  static Future<_ManualPayoutResult?> show(BuildContext context) =>
      showDialog<_ManualPayoutResult>(
        context: context,
        builder: (_) => const _ManualPayoutDialog(),
      );

  @override
  State<_ManualPayoutDialog> createState() => _ManualPayoutDialogState();
}

class _ManualPayoutDialogState extends State<_ManualPayoutDialog> {
  final TextEditingController _amount = TextEditingController();
  final TextEditingController _note = TextEditingController();

  @override
  void dispose() {
    _amount.dispose();
    _note.dispose();
    super.dispose();
  }

  int? get _paisa {
    final text = normalizeDigits(_amount.text).replaceAll(',', '').trim();
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
      title: const Text('Money that arrived'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'A lump sum with no breakdown cannot be matched automatically — '
            'there is nothing to match on but the number. You will choose which '
            'parcels it covers.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Amount (৳)',
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          LabelledField(
            label: 'Note',
            controller: _note,
            hint: 'Optional — bKash from Steadfast',
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: (_paisa ?? 0) > 0
              ? () => Navigator.of(context).pop(
                  _ManualPayoutResult(
                    amountPaisa: _paisa!,
                    note: _note.text.trim(),
                  ),
                )
              : null,
          child: const Text('Record'),
        ),
      ],
    );
  }
}

/// Payments Steadfast sent, as Steadfast reports them.
///
/// Shown *above* the payout list rather than mixed into it, because it answers
/// a different question: "has the courier paid me?" versus "what have we tied
/// to a parcel?". Two facts are carried deliberately:
///
/// * the source is named — `Steadfast API`, not an anonymous row — so a seller
///   can tell an imported payment from a statement they uploaded;
/// * a payment whose field names were *inferred* says so. Steadfast documents
///   no response schema for its payments endpoints, and a seller comparing
///   these numbers against their courier portal deserves to know that rather
///   than discovering it during a disagreement.
class _SteadfastPaymentsSection extends ConsumerStatefulWidget {
  const _SteadfastPaymentsSection();

  @override
  ConsumerState<_SteadfastPaymentsSection> createState() =>
      _SteadfastPaymentsSectionState();
}

class _SteadfastPaymentsSectionState
    extends ConsumerState<_SteadfastPaymentsSection> {
  bool _syncing = false;

  Future<void> _sync() async {
    if (_syncing) {
      return;
    }
    setState(() => _syncing = true);
    try {
      await ref.read(courierRepositoryProvider).syncPayments();
      ref.invalidate(providerPaymentsProvider);
      await ref.read(payoutListProvider.notifier).refresh();
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _syncing = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final account = ref.watch(courierAccountProvider('steadfast'));
    final connected = account.maybeWhen(
      data: (value) => value?.status.canBook ?? false,
      orElse: () => false,
    );
    if (!connected) {
      // No account: nothing to sync, and a disabled button explaining an
      // absent integration is worse than no section at all.
      return const SizedBox.shrink();
    }

    final payments = ref.watch(providerPaymentsProvider);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SectionHeader(
          title: 'From Steadfast',
          subtitle: 'Payments the courier says it has sent',
          actionLabel: _syncing ? 'Syncing…' : 'Sync now',
          onAction: _syncing ? null : _sync,
        ),
        payments.when(
          loading: () => const SkeletonLoader(height: 64),
          error: (_, __) => const SizedBox.shrink(),
          data: (rows) => rows.isEmpty
              ? Text(
                  'No payments synced yet.',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                )
              : Column(
                  children: <Widget>[
                    for (final payment in rows.take(5))
                      _ProviderPaymentRow(payment: payment),
                  ],
                ),
        ),
      ],
    );
  }
}

class _ProviderPaymentRow extends StatelessWidget {
  const _ProviderPaymentRow({required this.payment});

  final couriers.ProviderPayment payment;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    payment.providerReference ?? payment.providerPaymentId,
                    style: EcomsbdType.label,
                  ),
                ),
                Text(
                  payment.totalPaisa == null
                      ? '—'
                      : Money(payment.totalPaisa!).format(),
                  style: EcomsbdType.sectionTitle,
                ),
              ],
            ),
            const SizedBox(height: 4),
            Wrap(
              spacing: EcomsbdSpacing.xs,
              runSpacing: 4,
              children: <Widget>[
                const StatusChip(
                  label: 'Steadfast API',
                  tone: Tone.info,
                  showIcon: false,
                ),
                StatusChip(
                  label: payment.stateLabel,
                  tone: payment.needsAttention ? Tone.warning : Tone.good,
                  showIcon: false,
                ),
                if (payment.consignmentCount != null)
                  StatusChip(
                    label: '${payment.consignmentCount} parcels',
                    tone: Tone.neutral,
                    showIcon: false,
                  ),
              ],
            ),
            if (payment.needsAttention) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                payment.errorMessage ??
                    'Steadfast changed its copy of this payment after we '
                        'imported it. Nothing already settled has been '
                        'rewritten — someone needs to look.',
                style: EcomsbdType.caption.copyWith(color: Tone.warning.ink),
              ),
            ],
            if (payment.schemaUnverified) ...<Widget>[
              const SizedBox(height: 4),
              Text(
                'Steadfast does not publish the format of this response, so '
                'these field names are our best reading of it.',
                style: EcomsbdType.caption.copyWith(
                  color: EcomsbdColors.muted2,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
