import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../shared/responsive.dart';

/// Money the seller spent that no parcel carries by itself.
///
/// Master spec section 86 is the whole shape of this screen: **recording is
/// not allocating.** Entering ৳5,000 of Facebook spend changes no profit
/// figure until the seller says which parcels it paid for, and the screen says
/// so on every unallocated row. The alternative — quietly smearing it across
/// whatever was delivered — would move numbers the seller had already read
/// without telling them.
class ExpensesScreen extends ConsumerWidget {
  const ExpensesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(expenseListProvider);
    final controller = ref.read(expenseListProvider.notifier);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => ExpenseSheet.show(context),
        icon: const Icon(Icons.add_rounded),
        label: const Text('Add expense'),
      ),
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
                          eyebrow: 'Costs no single parcel carries',
                          title: 'Expenses',
                          description:
                              'Recording an expense changes no profit figure. '
                              'It reaches your orders only when you allocate '
                              'it.',
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
                        label: 'All',
                        selected: controller.kind == null,
                        onChanged: (_) => controller.setKind(null),
                      ),
                      for (final kind in ExpenseKind.values)
                        FilterToggle(
                          label: kind.label,
                          selected: controller.kind == kind,
                          onChanged: (selected) =>
                              controller.setKind(selected ? kind : null),
                        ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.sm),
                  PagedListBody<Expense>(
                    state: state,
                    onRetry: controller.refresh,
                    onLoadMore: controller.loadMore,
                    emptyIcon: Icons.receipt_long_outlined,
                    emptyTitle: 'No expenses yet',
                    emptyMessage:
                        'Ad spend, packaging and rent live here. Until you '
                        'add them, your profit figure is contribution profit '
                        'only — what each parcel earned before the costs of '
                        'running the shop.',
                    itemBuilder: (context, expense) => Padding(
                      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
                      child: ExpenseCard(expense: expense),
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

class ExpenseCard extends ConsumerStatefulWidget {
  const ExpenseCard({required this.expense, super.key});

  final Expense expense;

  @override
  ConsumerState<ExpenseCard> createState() => _ExpenseCardState();
}

class _ExpenseCardState extends ConsumerState<ExpenseCard> {
  bool _busy = false;

  Future<void> _allocate() async {
    final expense = widget.expense;
    String? reason;

    // Re-allocating rewrites profit figures the seller has already read, so
    // section 86 requires a reason. Asking for it here rather than letting the
    // server reject the call keeps the explanation attached to the act.
    if (expense.isAllocated) {
      reason = await _askReason();
      if (reason == null) return;
    }

    setState(() => _busy = true);
    try {
      final result = await ref
          .read(expenseListProvider.notifier)
          .allocate(expense.id, reason: reason);
      ref.invalidate(profitReportProvider);
      ref.invalidate(homeMetricsProvider);
      ref.invalidate(productProfitProvider);

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              result.reachedNothing
                  ? 'Nothing was delivered in that period, so this cost '
                        'reached no parcel. It stays visible as unallocated.'
                  : 'Spread ${result.allocated.format()} across '
                        '${result.parcelCount} parcel'
                        '${result.parcelCount == 1 ? '' : 's'}.',
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
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<String?> _askReason() async {
    final controller = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Why re-allocate?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(
              'This changes profit figures you have already seen. The old '
              'ones stay readable, with your reason next to them.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            TextField(
              controller: controller,
              autofocus: true,
              maxLength: 200,
              decoration: const InputDecoration(
                hintText: 'Equal split was misleading for these order sizes',
              ),
            ),
          ],
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              final text = controller.text.trim();
              if (text.isNotEmpty) Navigator.of(context).pop(text);
            },
            child: const Text('Re-allocate'),
          ),
        ],
      ),
    );
    controller.dispose();
    return reason;
  }

  @override
  Widget build(BuildContext context) {
    final expense = widget.expense;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: Text(expense.description, style: EcomsbdType.bodyStrong),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Text(expense.amount.format(), style: EcomsbdType.money),
            ],
          ),
          const SizedBox(height: 3),
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  '${expense.kind.label} · '
                  '${_period(expense)}',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Flexible(child: _statusChip(expense)),
            ],
          ),
          if (expense.unallocated.paisa > 0 && expense.kind.isAllocatable) ...[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              '${expense.unallocated.format()} has not reached any parcel. '
              'Until it does, it sits below contribution profit rather than '
              'being spread across unrelated orders.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (expense.kind.isAllocatable) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                onPressed: _busy ? null : _allocate,
                icon: _busy
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.call_split_rounded, size: 18),
                label: Text(
                  expense.isAllocated
                      ? 'Allocate again'
                      : 'Allocate to parcels',
                ),
              ),
            ),
          ] else ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              // Section 86 warns against pretending fixed-cost allocation is
              // accounting-grade, so this app does not spread rent per parcel.
              'Fixed costs are not spread across parcels. They come off '
              'below contribution profit, where they belong.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }

  static Widget _statusChip(Expense expense) {
    if (!expense.kind.isAllocatable) {
      return const StatusChip(label: 'Below the line', tone: Tone.neutral);
    }
    if (expense.reachedNothing) {
      return const StatusChip(label: 'Reached no parcel', tone: Tone.warning);
    }
    if (expense.isAllocated) {
      return StatusChip(
        label: '${expense.allocated.formatCompact()} allocated',
        tone: Tone.good,
      );
    }
    return const StatusChip(label: 'Not allocated', tone: Tone.info);
  }

  static String _period(Expense expense) {
    final start = _day(expense.periodStart);
    final end = _day(expense.periodEnd);
    return start == end ? start : '$start to $end';
  }

  static String _day(DateTime value) =>
      '${value.day.toString().padLeft(2, '0')}/'
      '${value.month.toString().padLeft(2, '0')}';
}

/// Add one expense.
class ExpenseSheet extends ConsumerStatefulWidget {
  const ExpenseSheet({super.key});

  static Future<bool> show(BuildContext context) async {
    final result = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => const ExpenseSheet(),
    );
    return result ?? false;
  }

  @override
  ConsumerState<ExpenseSheet> createState() => _ExpenseSheetState();
}

class _ExpenseSheetState extends ConsumerState<ExpenseSheet> {
  final TextEditingController _amount = TextEditingController();
  final TextEditingController _description = TextEditingController();

  ExpenseKind _kind = ExpenseKind.adSpend;
  AllocationMethod _method = AllocationMethod.equalPerDeliveredOrder;
  DateTimeRange? _period;
  bool _busy = false;
  ApiError? _error;

  @override
  void dispose() {
    _amount.dispose();
    _description.dispose();
    super.dispose();
  }

  /// Paisa from a taka amount. Parsed here rather than sent as a decimal so
  /// no rounding happens between the seller's keyboard and the ledger.
  int? get _amountPaisa {
    final text = _amount.text.trim();
    if (text.isEmpty) return null;
    final taka = double.tryParse(text);
    if (taka == null || taka <= 0) return null;
    return (taka * 100).round();
  }

  Future<void> _save() async {
    final paisa = _amountPaisa;
    final description = _description.text.trim();
    if (paisa == null || description.isEmpty) return;

    final period =
        _period ?? DateTimeRange(start: DateTime.now(), end: DateTime.now());

    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await ref
          .read(expenseListProvider.notifier)
          .record(
            kind: _kind,
            amountPaisa: paisa,
            periodStart: period.start,
            periodEnd: period.end,
            description: description,
            method: _method,
          );
      ref.invalidate(profitReportProvider);
      if (mounted) Navigator.of(context).pop(true);
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: Container(
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
              const Text('Add an expense', style: EcomsbdType.sectionTitle),
              const SizedBox(height: 3),
              Text(
                'This is recorded, not applied. Nothing on your Insights '
                'screen moves until you allocate it.',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              Wrap(
                spacing: EcomsbdSpacing.xs,
                runSpacing: EcomsbdSpacing.xs,
                children: <Widget>[
                  for (final kind in ExpenseKind.values)
                    FilterToggle(
                      label: kind.label,
                      selected: _kind == kind,
                      onChanged: (_) => setState(() => _kind = kind),
                    ),
                ],
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              LabelledField(
                label: 'Amount',
                controller: _amount,
                hint: 'Taka, e.g. 5000',
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              LabelledField(
                label: 'What was it for',
                controller: _description,
                hint: 'Facebook boost, poly bags, shop rent',
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              _PeriodField(
                period: _period,
                onChanged: (value) => setState(() => _period = value),
              ),
              if (_kind.isAllocatable) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.md),
                const Text('How should it be split?', style: EcomsbdType.label),
                const SizedBox(height: EcomsbdSpacing.xs),
                Wrap(
                  spacing: EcomsbdSpacing.xs,
                  runSpacing: EcomsbdSpacing.xs,
                  children: <Widget>[
                    for (final method in <AllocationMethod>[
                      AllocationMethod.equalPerDeliveredOrder,
                      AllocationMethod.proportionalToRevenue,
                    ])
                      FilterToggle(
                        label: method.label,
                        selected: _method == method,
                        onChanged: (_) => setState(() => _method = method),
                      ),
                  ],
                ),
              ],
              if (_error != null) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                Text(
                  _error!.displayMessage,
                  style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
                ),
              ],
              const SizedBox(height: EcomsbdSpacing.lg),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: _busy || _amountPaisa == null ? null : _save,
                  child: Text(_busy ? 'Saving…' : 'Record expense'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The period an expense covers.
///
/// Required rather than optional, because it is what decides which parcels an
/// ad spend could possibly have paid for. Defaults to today, which is right
/// for the common case of entering yesterday's boost this morning.
class _PeriodField extends StatelessWidget {
  const _PeriodField({required this.period, required this.onChanged});

  final DateTimeRange? period;
  final ValueChanged<DateTimeRange> onChanged;

  @override
  Widget build(BuildContext context) {
    final label = period == null
        ? 'Today'
        : '${_day(period!.start)} to ${_day(period!.end)}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Text('Period it covers', style: EcomsbdType.label),
        const SizedBox(height: EcomsbdSpacing.xs),
        OutlinedButton.icon(
          onPressed: () async {
            final now = DateTime.now();
            final picked = await showDateRangePicker(
              context: context,
              firstDate: DateTime(now.year - 2),
              lastDate: now,
              initialDateRange: period,
            );
            if (picked != null) onChanged(picked);
          },
          icon: const Icon(Icons.date_range_rounded, size: 18),
          label: Text(label),
        ),
      ],
    );
  }

  static String _day(DateTime value) =>
      '${value.day.toString().padLeft(2, '0')}/'
      '${value.month.toString().padLeft(2, '0')}/${value.year}';
}
