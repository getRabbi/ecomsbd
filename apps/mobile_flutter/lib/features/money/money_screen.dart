import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../demo/demo_dashboard.dart';
import '../../design/charts/bar_charts.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/demo_data_notice.dart';
import '../shared/responsive.dart';

/// The Money tab — the paid core.
///
/// Master spec section 3: COD outstanding, expected today, overdue, settled
/// this month, unmatched, plus aging and courier-wise outstanding.
///
/// Two rules from the spec shape what is shown:
/// section 82 — an amount-only candidate is never auto-matched, so ambiguous
/// lines appear as suggestions requiring review; and section 85 — imported or
/// inferred figures carry an estimate marker rather than being printed as exact.
class MoneyScreen extends ConsumerWidget {
  const MoneyScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return ListView(
      padding: EdgeInsets.fromLTRB(
        EcomsbdSpacing.page,
        topInset + EcomsbdTouch.minTarget + EcomsbdSpacing.lg,
        EcomsbdSpacing.page,
        EcomsbdSpacing.bottomNavClearance,
      ),
      children: <Widget>[
        const PageHeader(
          eyebrow: 'The paid core',
          title: 'COD money control',
          description:
              'Receivables, settlements, aging, mismatch and ledger-backed truth.',
        ),
        const DemoDataNotice(
          detail:
              'Demo data — real figures arrive with the money endpoints '
              'in Phase D.',
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        HeroMoneyCard(
          eyebrow: 'Outstanding COD',
          amount: DemoDashboard.codOutstanding,
          subtitle: '61 eligible/unsettled parcels · 3 data sources',
          trailing: StatusChip(
            label: '${DemoDashboard.overdue.format()} overdue',
            tone: Tone.bad,
          ),
          kpis: DemoDashboard.homeKpis,
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        ResponsiveGrid(
          minTileWidth: 320,
          maxColumns: 2,
          spacing: EcomsbdSpacing.sm,
          children: <Widget>[
            PremiumChartCard(
              title: 'COD aging',
              subtitle: 'How long money has been collectible',
              child: Column(
                children: <Widget>[
                  for (final bucket in DemoDashboard.codAging)
                    MoneyAgingRow(
                      label: bucket.label,
                      amount: bucket.amount,
                      fraction: bucket.fraction,
                      tone: bucket.tone,
                    ),
                ],
              ),
            ),
            const PremiumChartCard(
              title: 'Settled vs due',
              subtitle: 'Last 6 settlement days',
              trailing: StatusChip(label: '92.4% matched', tone: Tone.good),
              child: SettledVsDueChart(bars: DemoDashboard.settledVsDue),
            ),
          ],
        ),
        const SectionHeader(
          title: 'Courier receivables',
          subtitle: 'Actual where available; imported/estimated clearly marked',
        ),
        const _CourierReceivables(),
        const SectionHeader(
          title: 'Reconciliation',
          subtitle:
              'Precision before recall — a wrong auto-match is worse '
              'than an unresolved item',
        ),
        for (final finding in DemoDashboard.findings)
          Padding(
            padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: FindingCard(
              title: finding.title,
              description: finding.description,
              status: StatusChip(
                label: finding.amountLabel,
                tone: finding.tone,
              ),
              tags: finding.tags,
            ),
          ),
      ],
    );
  }
}

class _CourierReceivables extends StatelessWidget {
  const _CourierReceivables();

  @override
  Widget build(BuildContext context) {
    return const Column(
      children: <Widget>[
        // The tilde and the "Estimated" badge are not decoration: an imported
        // statement total is not the same fact as an API-confirmed settlement,
        // and master spec section 85 requires the difference to be visible.
        _ReceivableRow(
          courier: 'Steadfast',
          detail: '35 unsettled',
          outstanding: '৳41,200',
          overdue: '৳2,320',
          quality: DataQuality.actual,
        ),
        _ReceivableRow(
          courier: 'Pathao',
          detail: 'imported history',
          outstanding: '~৳22,500',
          overdue: '~৳3,900',
          quality: DataQuality.estimated,
        ),
        _ReceivableRow(
          courier: 'RedX',
          detail: 'statement only',
          outstanding: '~৳18,750',
          overdue: '~৳1,810',
          quality: DataQuality.estimated,
        ),
      ],
    );
  }
}

class _ReceivableRow extends StatelessWidget {
  const _ReceivableRow({
    required this.courier,
    required this.detail,
    required this.outstanding,
    required this.overdue,
    required this.quality,
  });

  final String courier;
  final String detail;
  final String outstanding;
  final String overdue;
  final DataQuality quality;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      child: GlassListRow(
        leading: RowIcon(label: courier.substring(0, 1)),
        title: courier,
        subtitle: '$detail · overdue $overdue',
        trailing: Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(outstanding, style: EcomsbdType.money),
            const SizedBox(height: 3),
            DataQualityBadge(quality: quality),
          ],
        ),
      ),
    );
  }
}
