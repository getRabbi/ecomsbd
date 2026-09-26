import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/commerce/repository_support.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../couriers/courier_compare_screen.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'cases_screen.dart';
import 'cashflow_screen.dart';
import 'money_sections.dart';
import 'payouts_screen.dart';
import 'receivables_screen.dart';

/// The Money tab.
///
/// Master spec section 1.1: the seller must be able to see, every day, how much
/// money is with the courier and what has not arrived. Everything on this
/// screen is a server figure derived from the ledger — nothing is computed on
/// the device, because a phone that worked out its own balance would eventually
/// disagree with the one the shop is actually owed (section 64).
class MoneyScreen extends ConsumerWidget {
  const MoneyScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(moneySummaryProvider);
    // The sections under the summary are their own requests. They start with
    // it, not once it has arrived, which doubled the time to a complete tab.
    ref
      ..listen(courierBalancesProvider, (_, _) {})
      ..listen(reconciliationItemListProvider, (_, _) {});

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        ref.invalidate(moneySummaryProvider);
        ref.invalidate(courierBalancesProvider);
        ref.invalidate(profitReportProvider);
        ref.invalidate(rtoCouriersProvider);
        await ref.read(reconciliationItemListProvider.notifier).refresh();
      },
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          EcomsbdLayout.shellTopPadding(context),
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          PageHeader(
            eyebrow: context.tr('money.eyebrow'),
            title: context.tr('money.title'),
            description: context.tr('money.description'),
          ),
          ...async.when(
            loading: () => <Widget>[const DashboardSkeleton()],
            error: (error, _) => <Widget>[
              ErrorStateCard(
                error: ApiError.from(error),
                onRetry: () => ref.invalidate(moneySummaryProvider),
              ),
            ],
            data: (sourced) => _body(context, ref, sourced),
          ),
        ],
      ),
    );
  }

  List<Widget> _body(
    BuildContext context,
    WidgetRef ref,
    Sourced<MoneySummary> sourced,
  ) {
    final summary = sourced.value;

    return <Widget>[
      if (sourced.isStale) ...<Widget>[
        StaleDataNotice(
          fetchedAt: sourced.fetchedAt,
          onRetry: () => ref.invalidate(moneySummaryProvider),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      HeroMoneyCard(
        eyebrow: context.tr('money.withCourierNow'),
        amount: summary.outstanding,
        subtitle: context.trPlural(
          'money.unpaidParcels',
          summary.unpaidParcelCount,
        ),
        // Settled money is exact — it came off a statement — so no estimate
        // marker (master spec section 123).
        quality: DataQuality.actual,
        trailing: summary.overdue.isZero
            ? null
            : StatusChip(
                label: context.tr('money.overAWeek', <String, Object?>{
                  'amount': summary.overdue.formatCompact(),
                }),
                tone: Tone.bad,
                icon: Icons.schedule,
              ),
        kpis: <HeroKpi>[
          HeroKpi(
            label: context.tr('money.arrived'),
            value: summary.settled.formatCompact(),
          ),
          HeroKpi(
            label: context.tr('money.deducted'),
            value: summary.totalDeductions.formatCompact(),
          ),
          HeroKpi(
            label: context.tr('money.needsYou'),
            value: '${summary.openCaseCount}',
            tone: summary.openCaseCount > 0 ? Tone.warning : null,
          ),
        ],
      ),
      const SizedBox(height: EcomsbdSpacing.sm),
      if (summary.openCaseCount > 0) ...<Widget>[
        _CasesBanner(
          count: summary.openCaseCount,
          onOpen: () => Navigator.of(
            context,
          ).push(MaterialPageRoute<void>(builder: (_) => const CasesScreen())),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      if (!summary.unexplainedPayout.isZero) ...<Widget>[
        AlertStrip(
          icon: Icons.help_outline_rounded,
          tone: Tone.warning,
          title: context.tr('money.unexplained', <String, Object?>{
            'amount': summary.unexplainedPayout.format(),
          }),
          detail: context.tr('money.unexplainedDetail'),
          actionLabel: context.tr('money.payouts'),
          onAction: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const PayoutsScreen()),
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      const PayoutMismatchSection(),
      SectionHeader(
        title: context.tr('money.agingTitle'),
        subtitle: context.tr('money.agingSub'),
      ),
      _AgingCard(bands: summary.aging, total: summary.outstanding),
      const CourierReceivableSection(),
      SectionHeader(
        title: context.tr('money.deductionsTitle'),
        subtitle: context.tr('money.deductionsSub'),
      ),
      _DeductionsCard(summary: summary),
      const CourierSpendSection(),
      const SizedBox(height: 12),
      ResponsiveGrid(
        minTileWidth: 150,
        maxColumns: 2,
        spacing: 10,
        children: <Widget>[
          QuickActionTile(
            icon: Icons.receipt_long_outlined,
            title: context.tr('money.receivables'),
            subtitle: context.tr('money.receivablesSub'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const ReceivablesScreen(),
              ),
            ),
          ),
          QuickActionTile(
            icon: Icons.account_balance_wallet_outlined,
            title: context.tr('money.payouts'),
            subtitle: context.tr('money.payoutsSub'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const PayoutsScreen()),
            ),
          ),
          QuickActionTile(
            icon: Icons.waterfall_chart_outlined,
            title: context.tr('rcv.title'),
            subtitle: context.tr('rcv.tileSub'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => const CashflowScreen()),
            ),
          ),
          QuickActionTile(
            icon: Icons.local_shipping_outlined,
            title: context.tr('money.courierCosts'),
            subtitle: context.tr('money.courierCostsSub'),
            onTap: () => CourierCompareScreen.open(context),
          ),
        ],
      ),
    ];
  }
}

class _CasesBanner extends StatelessWidget {
  const _CasesBanner({required this.count, required this.onOpen});

  final int count;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) {
    return AlertStrip(
      icon: Icons.priority_high_rounded,
      tone: Tone.bad,
      title: context.trPlural('money.casesBanner', count),
      detail: context.tr('money.casesDetail'),
      actionLabel: context.tr('common.open'),
      onAction: onOpen,
    );
  }
}

class _AgingCard extends StatelessWidget {
  const _AgingCard({required this.bands, required this.total});

  final List<AgingBand> bands;
  final Money total;

  @override
  Widget build(BuildContext context) {
    if (total.isZero) {
      return GlassCard(
        child: Row(
          children: <Widget>[
            const Icon(
              Icons.check_circle_outline,
              size: 20,
              color: EcomsbdColors.green,
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: Text(
                context.tr('money.nothingWaiting'),
                style: EcomsbdType.body,
              ),
            ),
          ],
        ),
      );
    }

    return GlassCard(
      padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 4),
      child: Column(
        children: <Widget>[
          for (var i = 0; i < bands.length; i++)
            KpiLine(
              // The band already reads "8-14 days" and comes from the
              // server; the parcel count goes in the label.
              label: context.trPlural(
                'money.bandLabel',
                bands[i].parcelCount,
                <String, Object?>{'band': bands[i].displayLabel},
              ),
              value: bands[i].outstanding.format(),
              valueColor: bands[i].isOverdue && !bands[i].outstanding.isZero
                  ? EcomsbdColors.red
                  : null,
              isLast: i == bands.length - 1,
            ),
        ],
      ),
    );
  }
}

class _DeductionsCard extends StatelessWidget {
  const _DeductionsCard({required this.summary});

  final MoneySummary summary;

  @override
  Widget build(BuildContext context) {
    final rows = <({String label, Money amount, bool unknown})>[
      (
        label: context.tr('money.deduction.delivery'),
        amount: summary.courierCharge,
        unknown: false,
      ),
      (
        label: context.tr('money.deduction.codFee'),
        amount: summary.codFee,
        unknown: false,
      ),
      (
        label: context.tr('money.deduction.returnCharge'),
        amount: summary.returnCharge,
        unknown: false,
      ),
      (
        label: context.tr('money.deduction.notExplained'),
        amount: summary.unknownDeduction,
        unknown: true,
      ),
    ];

    return GlassCard(
      padding: const EdgeInsets.fromLTRB(15, 4, 15, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          for (var i = 0; i < rows.length; i++)
            KpiLine(
              label: rows[i].label,
              value: rows[i].amount.format(),
              labelColor: rows[i].unknown && !rows[i].amount.isZero
                  ? EcomsbdColors.amber
                  : null,
              isLast: i == rows.length - 1,
            ),
          if (summary.hasUnknownDeductions) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              // Master spec section 84: an unknown deduction stays visible
              // rather than being folded into "delivery charge".
              context.tr('money.unknownNote'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
            ),
          ],
          if (!summary.writeOff.isZero) ...<Widget>[
            const Divider(height: EcomsbdSpacing.lg),
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    context.tr('money.writtenOff'),
                    style: EcomsbdType.body.copyWith(color: EcomsbdColors.red),
                  ),
                ),
                MoneyText(
                  summary.writeOff,
                  style: EcomsbdType.bodyStrong.copyWith(
                    color: EcomsbdColors.red,
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
