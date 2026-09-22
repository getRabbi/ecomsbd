import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';
import '../../l10n/app_strings.dart';

/// Money → Reconciliation.
///
/// Two views of the same work. **Cases** are the things that need a person —
/// master spec section 16 requires a separate issue entity for each, because a
/// "delivered but unpaid" that exists only as a filter on a list is a problem
/// nobody is accountable for. **Parcels** answers the seller's three questions
/// for every parcel a statement touched: what the courier should have paid,
/// what it paid, and the difference.
///
/// Every figure on this screen is the server's. Nothing is added up here.
class CasesScreen extends ConsumerStatefulWidget {
  const CasesScreen({super.key});

  @override
  ConsumerState<CasesScreen> createState() => _CasesScreenState();
}

class _CasesScreenState extends ConsumerState<CasesScreen> {
  bool _busy = false;
  bool _parcels = false;

  void _snack(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  Future<void> _refreshAll() async {
    ref.invalidate(reconciliationSummaryProvider);
    ref.invalidate(moneySummaryProvider);
    await Future.wait(<Future<void>>[
      ref.read(caseListProvider.notifier).refresh(),
      if (_parcels) ref.read(reconciliationItemListProvider.notifier).refresh(),
    ]);
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
    } on ApiError catch (error) {
      _snack(error.displayMessage);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _scan() => _run(() async {
    final opened = await ref.read(moneyRepositoryProvider).scanForCases();
    await _refreshAll();
    if (mounted) {
      _snack(
        opened == 0
            ? context.tr('case.nothingNew')
            : context.tr('rv2.newCases', <String, Object?>{'count': opened}),
      );
    }
  });

  Future<void> _close(ReconciliationCase item, {required bool dismiss}) async {
    final resolution = await _ResolutionDialog.show(
      context,
      dismiss: dismiss,
      summary: item.summary,
    );
    if (resolution == null) {
      return;
    }
    await _run(() async {
      await ref
          .read(moneyRepositoryProvider)
          .updateCase(
            item.id,
            status: dismiss ? 'DISMISSED' : 'RESOLVED',
            resolution: resolution,
          );
      await _refreshAll();
    });
  }

  Future<void> _accept(ReconciliationItem item) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('rv2.acceptTitle')),
        content: Text(
          context.tr('rv2.acceptBody', <String, Object?>{
            'amount': item.chargesPending.format(),
          }),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(context.tr('common.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(context.tr('rv2.accept')),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await _run(() async {
      await ref.read(moneyRepositoryProvider).acceptCharges(item.id);
      await _refreshAll();
      if (mounted) {
        _snack(context.tr('rv2.accepted'));
      }
    });
  }

  Future<void> _openCase(ReconciliationCase item) async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => _CaseSheet(caseId: item.id, onChanged: _refreshAll),
    );
  }

  void _showParcels(bool value) {
    if (value == _parcels) {
      return;
    }
    setState(() => _parcels = value);
    if (value) {
      // Read lazily: a seller who only works cases never pays for this list.
      ref.read(reconciliationItemListProvider.notifier);
    }
  }

  @override
  Widget build(BuildContext context) {
    final cases = ref.watch(caseListProvider);
    final caseController = ref.read(caseListProvider.notifier);
    final summary = ref.watch(reconciliationSummaryProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: _refreshAll,
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
                        tooltip: context.tr('common.back'),
                      ),
                      Expanded(
                        child: PageHeader(
                          eyebrow: context.tr('case.eyebrow'),
                          title: context.tr('money.needsYou'),
                          description: context.tr('case.description'),
                        ),
                      ),
                      IconButton(
                        onPressed: _busy ? null : _scan,
                        icon: const Icon(Icons.refresh_rounded),
                        tooltip: context.tr('case.checkAgain'),
                      ),
                    ],
                  ),
                  // The headline figures are a bonus on top of the case list:
                  // if they cannot load, the list still works.
                  ...summary.maybeWhen(
                    data: (sourced) => sourced.value.isEmpty
                        ? const <Widget>[]
                        : <Widget>[
                            ReconciliationSummaryCard(summary: sourced.value),
                            const SizedBox(height: EcomsbdSpacing.sm),
                          ],
                    orElse: () => const <Widget>[],
                  ),
                  Wrap(
                    spacing: EcomsbdSpacing.xs,
                    children: <Widget>[
                      FilterToggle(
                        label: context.tr('rv2.tab.cases'),
                        selected: !_parcels,
                        onChanged: (_) => _showParcels(false),
                      ),
                      FilterToggle(
                        label: context.tr('rv2.tab.parcels'),
                        selected: _parcels,
                        onChanged: (_) => _showParcels(true),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  if (_parcels)
                    _ParcelList(busy: _busy, onAccept: _accept)
                  else ...<Widget>[
                    if (cases.isStale) ...<Widget>[
                      StaleDataNotice(
                        fetchedAt: cases.fetchedAt,
                        onRetry: caseController.refresh,
                      ),
                      const SizedBox(height: EcomsbdSpacing.sm),
                    ],
                    Wrap(
                      spacing: EcomsbdSpacing.xs,
                      runSpacing: EcomsbdSpacing.xs,
                      children: <Widget>[
                        FilterToggle(
                          label: context.tr('common.open'),
                          selected: caseController.status == 'OPEN',
                          onChanged: (selected) => caseController.setStatus(
                            selected ? 'OPEN' : null,
                          ),
                        ),
                        FilterToggle(
                          label: context.tr('case.sorted'),
                          selected: caseController.status == 'RESOLVED',
                          onChanged: (selected) => caseController.setStatus(
                            selected ? 'RESOLVED' : null,
                          ),
                        ),
                        FilterToggle(
                          label: context.tr('common.all'),
                          selected: caseController.status == null,
                          onChanged: (_) => caseController.setStatus(null),
                        ),
                      ],
                    ),
                    const SizedBox(height: EcomsbdSpacing.sm),
                    PagedListBody<ReconciliationCase>(
                      state: cases,
                      onRetry: caseController.refresh,
                      onLoadMore: caseController.loadMore,
                      emptyIcon: Icons.check_circle_outline,
                      emptyTitle: context.tr('case.emptyTitle'),
                      emptyMessage: context.tr('case.emptyBody'),
                      itemBuilder: (context, item) => CaseCard(
                        item: item,
                        busy: _busy,
                        onOpen: () => _openCase(item),
                        onResolve: () => _close(item, dismiss: false),
                        onDismiss: () => _close(item, dismiss: true),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// What should have arrived, what did, and the difference.
class ReconciliationSummaryCard extends StatelessWidget {
  const ReconciliationSummaryCard({required this.summary, super.key});

  final ReconciliationSummary summary;

  @override
  Widget build(BuildContext context) {
    final difference = summary.difference;
    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(context.tr('rv2.sum.title'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.sm),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: _Figure(
                  label: context.tr('rv2.sum.expected'),
                  amount: summary.expected,
                ),
              ),
              Expanded(
                child: _Figure(
                  label: context.tr('rv2.sum.actual'),
                  amount: summary.actual,
                ),
              ),
              Expanded(
                child: _Figure(
                  label: context.tr('rv2.sum.difference'),
                  amount: difference,
                  signed: true,
                ),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              StatusChip(
                label: '${context.tr('rv2.sum.matched')} ${summary.matched}',
                tone: Tone.good,
                showIcon: false,
              ),
              StatusChip(
                label:
                    '${context.tr('rv2.sum.discrepancies')} '
                    '${summary.discrepancies}',
                tone: summary.discrepancies > 0 ? Tone.bad : Tone.neutral,
                showIcon: false,
              ),
              StatusChip(
                label:
                    '${context.tr('rv2.sum.unmatched')} ${summary.unmatched}',
                tone: summary.unmatched > 0 ? Tone.warning : Tone.neutral,
                showIcon: false,
              ),
            ],
          ),
          if (!summary.unmatchedAmount.isZero) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              context.tr('rv2.sum.unplaced', <String, Object?>{
                'amount': summary.unmatchedAmount.format(),
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (!summary.chargesPending.isZero) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xxs),
            Text(
              context.tr('rv2.sum.pending', <String, Object?>{
                'amount': summary.chargesPending.format(),
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }
}

class _Figure extends StatelessWidget {
  const _Figure({
    required this.label,
    required this.amount,
    this.signed = false,
  });

  final String label;
  final Money amount;
  final bool signed;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Text(
          label,
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        const SizedBox(height: 2),
        FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: MoneyText(
            amount,
            style: EcomsbdType.bodyStrong,
            signed: signed,
            colorBySign: signed,
          ),
        ),
      ],
    );
  }
}

class _ParcelList extends ConsumerWidget {
  const _ParcelList({required this.busy, required this.onAccept});

  final bool busy;
  final ValueChanged<ReconciliationItem> onAccept;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(reconciliationItemListProvider);
    final controller = ref.read(reconciliationItemListProvider.notifier);

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
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
              label: context.tr('rv2.sum.discrepancies'),
              selected: controller.view == 'discrepancies',
              onChanged: (_) => controller.setView('discrepancies'),
            ),
            FilterToggle(
              label: context.tr('rv2.sum.unmatched'),
              selected: controller.view == 'unmatched',
              onChanged: (_) => controller.setView('unmatched'),
            ),
            FilterToggle(
              label: context.tr('common.all'),
              selected: controller.view == null,
              onChanged: (_) => controller.setView(null),
            ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        PagedListBody<ReconciliationItem>(
          state: state,
          onRetry: controller.refresh,
          onLoadMore: controller.loadMore,
          emptyIcon: Icons.fact_check_outlined,
          emptyTitle: context.tr('rv2.emptyTitle'),
          emptyMessage: context.tr('rv2.emptyBody'),
          itemBuilder: (context, item) => ReconciliationItemCard(
            item: item,
            busy: busy,
            onAccept: () => onAccept(item),
          ),
        ),
      ],
    );
  }
}

/// One parcel: expected against actual.
class ReconciliationItemCard extends StatelessWidget {
  const ReconciliationItemCard({
    required this.item,
    super.key,
    this.busy = false,
    this.onAccept,
  });

  final ReconciliationItem item;
  final bool busy;
  final VoidCallback? onAccept;

  Tone get _tone => switch (item.status) {
    'MATCHED' => Tone.good,
    'UNMATCHED' || 'NEEDS_REVIEW' => Tone.warning,
    'DUPLICATE' || 'RETURN_ADJUSTMENT' => Tone.neutral,
    _ => Tone.bad,
  };

  @override
  Widget build(BuildContext context) {
    final reference = item.reference ?? item.trackingCode;
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
                  reference ?? context.tr('rv2.noReference'),
                  style: EcomsbdType.bodyStrong,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Flexible(
                child: StatusChip(label: item.statusLabel, tone: _tone),
              ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          const _CompareHeader(),
          _CompareRow(
            label: context.tr('rv2.row.cod'),
            expected: item.expectedCod,
            actual: item.actualCod,
          ),
          _CompareRow(
            label: context.tr('rv2.row.charge'),
            expected: item.expectedCharge,
            actual: item.actualCharge,
          ),
          _CompareRow(
            label: context.tr('rv2.row.net'),
            expected: item.expectedNet,
            actual: item.actualNet,
            strong: true,
          ),
          if (item.difference != null && !item.difference!.isZero)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.xxs),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      context.tr('rv2.row.difference'),
                      style: EcomsbdType.body,
                    ),
                  ),
                  MoneyText(
                    item.difference!,
                    style: EcomsbdType.bodyStrong,
                    signed: true,
                    colorBySign: true,
                  ),
                ],
              ),
            ),
          if (item.expectedCod != null && !item.chargeVerified) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              context.tr('rv2.chargeUnverified'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
            ),
          ],
          if (item.unknownDeduction) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xxs),
            Text(
              context.tr('rv2.unknownDeduction'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
            ),
          ],
          if (item.hasPendingCharges && onAccept != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              context.tr('rv2.pendingCharges', <String, Object?>{
                'amount': item.chargesPending.format(),
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: busy ? null : onAccept,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
                child: Text(context.tr('rv2.accept')),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _CompareHeader extends StatelessWidget {
  const _CompareHeader();

  @override
  Widget build(BuildContext context) {
    final style = EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2);
    return Row(
      children: <Widget>[
        const Expanded(flex: 4, child: SizedBox.shrink()),
        Expanded(
          flex: 3,
          child: Text(
            context.tr('rv2.col.expected'),
            style: style,
            textAlign: TextAlign.end,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        Expanded(
          flex: 3,
          child: Text(
            context.tr('rv2.col.actual'),
            style: style,
            textAlign: TextAlign.end,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

class _CompareRow extends StatelessWidget {
  const _CompareRow({
    required this.label,
    required this.expected,
    required this.actual,
    this.strong = false,
  });

  final String label;
  final Money? expected;
  final Money actual;
  final bool strong;

  @override
  Widget build(BuildContext context) {
    final style = strong ? EcomsbdType.bodyStrong : EcomsbdType.body;
    return Padding(
      padding: const EdgeInsets.only(top: 2),
      child: Row(
        children: <Widget>[
          Expanded(
            flex: 4,
            child: Text(
              label,
              style: style,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          Expanded(
            flex: 3,
            child: Align(
              alignment: Alignment.centerRight,
              child: expected == null
                  ? Text('—', style: style)
                  : FittedBox(
                      fit: BoxFit.scaleDown,
                      child: MoneyText(expected!, style: style),
                    ),
            ),
          ),
          Expanded(
            flex: 3,
            child: Align(
              alignment: Alignment.centerRight,
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: MoneyText(actual, style: style),
              ),
            ),
          ),
        ],
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
    this.onOpen,
    this.onResolve,
    this.onDismiss,
  });

  final ReconciliationCase item;
  final bool busy;
  final VoidCallback? onOpen;
  final VoidCallback? onResolve;
  final VoidCallback? onDismiss;

  @override
  Widget build(BuildContext context) {
    final tone = item.isHighPriority ? Tone.bad : Tone.warning;

    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      onTap: onOpen,
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
            '${item.statusLabel} · '
            '${context.tr('rv2.openedAgo', <String, Object?>{'when': formatRelative(item.openedAt)})}',
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
                    child: Text(context.tr('case.notAProblem')),
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
                    child: Text(context.tr('case.sorted')),
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

/// A case with its history: add a note, or reopen one that was closed.
class _CaseSheet extends ConsumerStatefulWidget {
  const _CaseSheet({required this.caseId, required this.onChanged});

  final String caseId;

  /// Refreshes the list behind the sheet after a change.
  final Future<void> Function() onChanged;

  @override
  ConsumerState<_CaseSheet> createState() => _CaseSheetState();
}

class _CaseSheetState extends ConsumerState<_CaseSheet> {
  final TextEditingController _note = TextEditingController();
  late Future<CaseDetail> _detail = _load();
  bool _busy = false;

  Future<CaseDetail> _load() =>
      ref.read(moneyRepositoryProvider).caseDetail(widget.caseId);

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _act(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      setState(() {
        _detail = _load();
      });
      await widget.onChanged();
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
    return Padding(
      padding: EdgeInsets.fromLTRB(
        EcomsbdSpacing.page,
        0,
        EcomsbdSpacing.page,
        MediaQuery.viewInsetsOf(context).bottom + EcomsbdSpacing.lg,
      ),
      child: FutureBuilder<CaseDetail>(
        future: _detail,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            final error = snapshot.error;
            return Text(
              error is ApiError ? error.displayMessage : '$error',
              style: EcomsbdType.body,
            );
          }
          final detail = snapshot.data;
          if (detail == null) {
            return const Padding(
              padding: EdgeInsets.all(EcomsbdSpacing.lg),
              child: Center(child: CircularProgressIndicator()),
            );
          }
          final item = detail.item;
          return SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(
                      child: StatusChip(
                        label: item.kindLabel,
                        tone: item.isOpen ? Tone.warning : Tone.neutral,
                      ),
                    ),
                    const SizedBox(width: EcomsbdSpacing.xs),
                    if (!item.amount.isZero)
                      MoneyText(item.amount, style: EcomsbdType.bodyStrong),
                  ],
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                Text(item.summary, style: EcomsbdType.body),
                if (detail.row != null) ...<Widget>[
                  const SizedBox(height: EcomsbdSpacing.sm),
                  ReconciliationItemCard(item: detail.row!),
                ],
                const SizedBox(height: EcomsbdSpacing.md),
                Text(
                  context.tr('rv2.case.history'),
                  style: EcomsbdType.bodyStrong,
                ),
                const SizedBox(height: EcomsbdSpacing.xs),
                for (final event in detail.events)
                  Padding(
                    padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: <Widget>[
                        Text(
                          '${event.actionLabel} · '
                          '${event.byPerson ? formatRelative(event.createdAt) : context.tr('rv2.case.automatic')}',
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                        ),
                        if (event.note != null)
                          Text(event.note!, style: EcomsbdType.body),
                      ],
                    ),
                  ),
                const SizedBox(height: EcomsbdSpacing.sm),
                LabelledField(
                  label: context.tr('common.note'),
                  controller: _note,
                  maxLines: 2,
                  hint: context.tr('rv2.case.noteHint'),
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
                Row(
                  children: <Widget>[
                    if (!item.isOpen) ...<Widget>[
                      Expanded(
                        child: OutlinedButton(
                          onPressed: _busy
                              ? null
                              : () => _act(
                                  () => ref
                                      .read(moneyRepositoryProvider)
                                      .updateCase(item.id, status: 'OPEN')
                                      .then((_) {}),
                                ),
                          style: OutlinedButton.styleFrom(
                            minimumSize: const Size.fromHeight(
                              EcomsbdTouch.minTarget,
                            ),
                            shape: const StadiumBorder(),
                          ),
                          child: Text(context.tr('rv2.case.reopen')),
                        ),
                      ),
                      const SizedBox(width: EcomsbdSpacing.sm),
                    ],
                    Expanded(
                      child: FilledButton(
                        onPressed: _busy || _note.text.trim().isEmpty
                            ? null
                            : () => _act(() async {
                                await ref
                                    .read(moneyRepositoryProvider)
                                    .addCaseNote(item.id, _note.text.trim());
                                _note.clear();
                              }),
                        style: FilledButton.styleFrom(
                          minimumSize: const Size.fromHeight(
                            EcomsbdTouch.minTarget,
                          ),
                          shape: const StadiumBorder(),
                        ),
                        child: Text(context.tr('rv2.case.addNote')),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          );
        },
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
      title: Text(
        widget.dismiss
            ? context.tr('case.dismissTitle')
            : context.tr('common.whatHappened'),
      ),
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
            label: context.tr('common.note'),
            controller: _resolution,
            maxLines: 2,
            hint: widget.dismiss
                ? context.tr('case.noteHint1')
                : context.tr('case.noteHint2'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            // Closing a case with no explanation teaches nobody anything, and
            // the same problem comes back next month.
            context.tr('case.noteRequired'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(context.tr('common.cancel')),
        ),
        FilledButton(
          onPressed: _resolution.text.trim().isNotEmpty
              ? () => Navigator.of(context).pop(_resolution.text.trim())
              : null,
          child: Text(
            widget.dismiss
                ? context.tr('case.dismiss')
                : context.tr('case.markSorted'),
          ),
        ),
      ],
    );
  }
}
