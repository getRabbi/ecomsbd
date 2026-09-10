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
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return RefreshIndicator(
      onRefresh: () async => ref.invalidate(moneySummaryProvider),
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          topInset + EcomsbdTouch.minTarget + EcomsbdSpacing.lg,
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          const PageHeader(
            eyebrow: 'Courier → COD → payout → matched',
            title: 'Money',
            description: 'What the courier owes you, and what has arrived.',
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
                  title: 'Could not load your money',
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
        eyebrow: 'With the courier now',
        amount: summary.outstanding,
        subtitle:
            '${summary.unpaidParcelCount} delivered parcel'
            '${summary.unpaidParcelCount == 1 ? '' : 's'} not paid yet',
        // Settled money is exact — it came off a statement — so no estimate
        // marker (master spec section 123).
        quality: DataQuality.actual,
        trailing: summary.overdue.isZero
            ? null
            : StatusChip(
                label: '${summary.overdue.formatCompact()} over a week',
                tone: Tone.bad,
                icon: Icons.schedule,
              ),
        kpis: <HeroKpi>[
          HeroKpi(label: 'Arrived', value: summary.settled.formatCompact()),
          HeroKpi(
            label: 'Deducted',
            value: summary.totalDeductions.formatCompact(),
          ),
          HeroKpi(
            label: 'Needs you',
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
          provider: '${summary.unexplainedPayout.format()} unexplained',
          detail:
              'Money arrived that has not been tied to a parcel yet. Open the '
              'payout to match it.',
          tone: Tone.info,
          actionLabel: 'Payouts',
          onAction: () => Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const PayoutsScreen()),
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      const SectionHeader(
        title: 'How long it has been waiting',
        subtitle: 'Outstanding COD by age',
      ),
      _AgingCard(bands: summary.aging, total: summary.outstanding),
      const SectionHeader(
        title: 'What the courier took',
        subtitle: 'Charges deducted from your money',
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
            title: 'Receivables',
            subtitle: 'Parcel by parcel',
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const ReceivablesScreen(),
              ),
            ),
          ),
          QuickActionTile(
            icon: Icons.account_balance_wallet_outlined,
            title: 'Payouts',
            subtitle: 'Statements and matching',
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
      provider: '$count thing${count == 1 ? '' : 's'} need you',
      detail:
          'Money that did not arrive, arrived short, or could not be placed.',
      tone: Tone.warning,
      actionLabel: 'Open',
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
      return const GlassCard(
        child: Row(
          children: <Widget>[
            Icon(
              Icons.check_circle_outline,
              size: 20,
              color: EcomsbdColors.green,
            ),
            SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: Text(
                'Nothing is waiting. Every delivered parcel has been paid.',
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
                // The band already reads "8-14 days"; the parcel count goes in
                // the label so the row stays one line at 360dp.
                label:
                    '${band.label} · ${band.parcelCount} parcel'
                    '${band.parcelCount == 1 ? '' : 's'}',
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
        label: 'Delivery charges',
        amount: summary.courierCharge,
        unknown: false,
      ),
      (label: 'COD fees', amount: summary.codFee, unknown: false),
      (label: 'Return charges', amount: summary.returnCharge, unknown: false),
      (label: 'Not explained', amount: summary.unknownDeduction, unknown: true),
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
              'We could not tell what the courier took this for. It is shown '
              'separately so you can ask them.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
            ),
          ],
          if (!summary.writeOff.isZero) ...<Widget>[
            const Divider(height: EcomsbdSpacing.lg),
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(
                    'Written off',
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
