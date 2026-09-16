import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/repository_support.dart';
import '../../data/money/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'cases_screen.dart';
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
    final isOffline = ref.watch(isOfflineProvider);

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async => ref.invalidate(moneySummaryProvider),
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
          if (isOffline) ...<Widget>[
            const OfflineBanner(),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
          ...async.when(
            loading: () => <Widget>[const DashboardSkeleton()],
            error: (error, _) => <Widget>[
              if (error is ApiError)
                ErrorStateCard(
                  error: error,
                  onRetry: () => ref.invalidate(moneySummaryProvider),
                )
              else
                EmptyState(
                  icon: Icons.error_outline,
                  title: context.tr('money.couldNotLoadTitle'),
                  message: '$error',
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
        ProviderHealthBanner(
          provider: context.tr('money.unexplained', <String, Object?>{
            'amount': summary.unexplainedPayout.format(),
          }),
          detail: context.tr('money.unexplainedDetail'),
          tone: Tone.info,
          actionLabel: context.tr('money.payouts'),
          onAction: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const PayoutsScreen()),
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      SectionHeader(
        title: context.tr('money.agingTitle'),
        subtitle: context.tr('money.agingSub'),
      ),
      _AgingCard(bands: summary.aging, total: summary.outstanding),
      SectionHeader(
        title: context.tr('money.deductionsTitle'),
        subtitle: context.tr('money.deductionsSub'),
      ),
      _DeductionsCard(summary: summary),
      const SizedBox(height: EcomsbdSpacing.md),
      ResponsiveGrid(
        minTileWidth: 150,
        maxColumns: 2,
        spacing: EcomsbdSpacing.xs,
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
    return ProviderHealthBanner(
      provider: context.trPlural('money.casesBanner', count),
      detail: context.tr('money.casesDetail'),
      tone: Tone.warning,
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
      child: Column(
        children: <Widget>[
          for (final band in bands)
            Padding(
              padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
              child: MoneyAgingRow(
                // The band already reads "8-14 days" and comes from the
                // server; the parcel count goes in the label so the row stays
                // one line at 360dp.
                label: context.trPlural(
                  'money.bandLabel',
                  band.parcelCount,
                  <String, Object?>{'band': band.label},
                ),
                amount: band.outstanding,
                fraction: total.paisa == 0
                    ? 0
                    : band.outstanding.paisa / total.paisa,
                tone: band.isOverdue ? Tone.bad : Tone.info,
              ),
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          for (final row in rows)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      row.label,
                      style: EcomsbdType.body.copyWith(
                        color: row.unknown && !row.amount.isZero
                            ? EcomsbdColors.amber
                            : null,
                      ),
                    ),
                  ),
                  MoneyText(row.amount, style: EcomsbdType.bodyStrong),
                ],
              ),
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
