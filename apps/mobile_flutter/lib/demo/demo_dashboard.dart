import 'package:flutter/material.dart';

import '../core/money.dart';
import '../design/charts/bar_charts.dart';
import '../design/charts/donut_chart.dart';
import '../design/charts/line_chart.dart';
import '../design/components/badges.dart';
import '../design/components/cards.dart';
import '../design/components/order_card.dart';
import '../design/tokens.dart';

/// UI-development fixtures. **Not real data.**
///
/// See `lib/demo/README.md`. Nothing in the production data layer imports this
/// file, and no repository falls back to it: a screen with no data shows an
/// empty state, never invented money.
///
/// Figures mirror the locked UI prototype so the Flutter screens can be
/// compared against the reference.
@immutable
class DemoDashboard {
  const DemoDashboard._();

  static const String shopName = 'Noor Fashion';
  static const String shopSubtitle =
      'Facebook seller · Dhaka · 46 orders today · live operations synced';

  static const List<String> heroChips = <String>[
    'Steadfast healthy',
    '৳2.84L settled this month',
    'Starter plan',
    'Last sync 1m ago',
  ];

  // --- money ----------------------------------------------------------------

  static const Money codOutstanding = Money(8745000);
  static const Money expectedToday = Money(2130000);
  static const Money settledThisMonth = Money(28450000);
  static const Money mismatch = Money(-424000);
  static const Money overdue = Money(875000);
  static const Money protectedProfit = Money(1187000);
  static const Money contributionProfit7d = Money(6140000);
  static const Money returnLoss = Money(-845000);

  static const String codOutstandingSubtitle =
      '61 delivered/eligible parcels · across 3 courier sources';

  static List<HeroKpi> get homeKpis => <HeroKpi>[
    HeroKpi(
      label: 'Expected today',
      value: expectedToday.formatCompact(),
      caption: '18 parcels',
    ),
    HeroKpi(
      label: 'Settled this month',
      value: settledThisMonth.formatCompact(),
      caption: '96 exact matches',
    ),
    HeroKpi(
      label: 'Mismatch',
      value: mismatch.formatCompact(),
      caption: '5 cases need review',
      tone: Tone.bad,
    ),
  ];

  // --- attention ------------------------------------------------------------

  static const List<AttentionItem> attentionItems = <AttentionItem>[
    AttentionItem(
      title: '3 delivered orders still unpaid',
      detail: 'Expected COD ৳3,940 · oldest parcel is 9 days since delivery.',
      tone: Tone.bad,
      icon: Icons.payments_outlined,
    ),
    AttentionItem(
      title: '2 payout lines are short by ৳270',
      detail: 'Exact parcel references matched; deductions remain unexplained.',
      tone: Tone.bad,
      icon: Icons.report_gmailerrorred_outlined,
    ),
    AttentionItem(
      title: '4 parcels are stalled 10+ days',
      detail: 'COD exposure ৳5,200 · provider refresh queued.',
      tone: Tone.warning,
      icon: Icons.autorenew_rounded,
    ),
  ];

  // --- charts ---------------------------------------------------------------

  static const List<TrendPoint> profitTrend = <TrendPoint>[
    TrendPoint(label: 'Thu', value: 620000),
    TrendPoint(label: 'Fri', value: 745000),
    TrendPoint(label: 'Sat', value: 690000),
    TrendPoint(label: 'Sun', value: 880000),
    TrendPoint(label: 'Mon', value: 940000),
    TrendPoint(label: 'Tue', value: 1120000),
    TrendPoint(label: 'Wed', value: 1145000),
  ];

  static const List<DonutSlice> codComposition = <DonutSlice>[
    DonutSlice(label: 'Due normally', value: 56, color: EcomsbdColors.green),
    DonutSlice(label: 'Watch', value: 21, color: EcomsbdColors.amber),
    DonutSlice(label: 'Overdue', value: 11, color: EcomsbdColors.red),
    DonutSlice(
      label: 'Unmatched',
      value: 12,
      color: EcomsbdColors.donutRemainder,
    ),
  ];

  static const List<FunnelStage> deliveryFunnel = <FunnelStage>[
    FunnelStage(label: 'Booked', count: 284),
    FunnelStage(label: 'Picked up', count: 273),
    FunnelStage(label: 'Delivered', count: 236),
    FunnelStage(
      label: 'Returned',
      count: 37,
      colors: <Color>[Color(0xFFE06F73), EcomsbdColors.red],
    ),
    FunnelStage(
      label: 'Unresolved',
      count: 11,
      colors: <Color>[Color(0xFF9AA4AE), Color(0xFF7E8892)],
    ),
  ];

  static const List<HorizontalBarDatum>
  topProductsByProfit = <HorizontalBarDatum>[
    HorizontalBarDatum(
      label: 'Red Abaya',
      value: 1820000,
      displayValue: '৳18.2k',
    ),
    HorizontalBarDatum(
      label: 'Black Abaya',
      value: 1370000,
      displayValue: '৳13.7k',
    ),
    HorizontalBarDatum(label: 'Watch X1', value: 960000, displayValue: '৳9.6k'),
    HorizontalBarDatum(label: 'Bag A2', value: 510000, displayValue: '৳5.1k'),
  ];

  static const List<HorizontalBarDatum> returnPressure = <HorizontalBarDatum>[
    HorizontalBarDatum(label: 'Black Abaya', value: 22, displayValue: '22%'),
    HorizontalBarDatum(label: 'Savar', value: 19, displayValue: '19%'),
    HorizontalBarDatum(label: 'Refused', value: 9, displayValue: '9'),
    HorizontalBarDatum(label: 'Wrong size', value: 6, displayValue: '6'),
  ];

  static const List<HorizontalBarDatum> profitBridge = <HorizontalBarDatum>[
    HorizontalBarDatum(
      label: 'Revenue',
      value: 216000,
      displayValue: '৳216k',
      colors: <Color>[Color(0xFF57B37D), EcomsbdColors.green],
    ),
    HorizontalBarDatum(
      label: 'COGS',
      value: 101000,
      displayValue: '-৳101k',
      colors: <Color>[Color(0xFFE46F74), EcomsbdColors.red],
    ),
    HorizontalBarDatum(
      label: 'Courier',
      value: 27000,
      displayValue: '-৳27k',
      colors: <Color>[Color(0xFFE46F74), EcomsbdColors.red],
    ),
    HorizontalBarDatum(
      label: 'Ads',
      value: 19000,
      displayValue: '-৳19k',
      colors: <Color>[Color(0xFFE46F74), EcomsbdColors.red],
    ),
    HorizontalBarDatum(
      label: 'Return loss',
      value: 8500,
      displayValue: '-৳8.5k',
      colors: <Color>[Color(0xFFE46F74), EcomsbdColors.red],
    ),
    HorizontalBarDatum(label: 'Profit', value: 61400, displayValue: '৳61.4k'),
  ];

  static const List<ComparisonBar> settledVsDue = <ComparisonBar>[
    ComparisonBar(label: '4 Sep', expected: 6100000, received: 5300000),
    ComparisonBar(label: '5 Sep', expected: 7200000, received: 6400000),
    ComparisonBar(label: '6 Sep', expected: 8400000, received: 7800000),
    ComparisonBar(label: '7 Sep', expected: 5700000, received: 5000000),
    ComparisonBar(label: '8 Sep', expected: 9100000, received: 8700000),
    ComparisonBar(label: '9 Sep', expected: 8100000, received: 7400000),
  ];

  // --- COD aging ------------------------------------------------------------

  static const List<({String label, Money amount, double fraction, Tone tone})>
  codAging = <({String label, Money amount, double fraction, Tone tone})>[
    (
      label: '0–2 days',
      amount: Money(4520000),
      fraction: 0.86,
      tone: Tone.info,
    ),
    (
      label: '3–5 days',
      amount: Money(1870000),
      fraction: 0.47,
      tone: Tone.info,
    ),
    (
      label: '6–10 days',
      amount: Money(890000),
      fraction: 0.28,
      tone: Tone.warning,
    ),
    (label: '10+ days', amount: Money(465000), fraction: 0.16, tone: Tone.bad),
  ];

  // --- courier health -------------------------------------------------------

  static const List<({String name, String detail, String value, String unit})>
  courierHealth = <({String name, String detail, String value, String unit})>[
    (
      name: 'Steadfast',
      detail: '90.5% success · 1.9d median · 2.1d settlement',
      value: '৳86',
      unit: '/delivered',
    ),
    (
      name: 'Pathao',
      detail: 'Imported history · not connected',
      value: '42',
      unit: 'samples',
    ),
    (
      name: 'RedX',
      detail: 'Insufficient reliable sample',
      value: '—',
      unit: 'no rank',
    ),
  ];

  // --- orders ---------------------------------------------------------------

  static List<DemoOrder> get orders => const <DemoOrder>[
    DemoOrder(
      reference: 'CP-20260909-0042',
      customerName: 'Nusrat Jahan',
      statusLabel: 'In transit',
      statusTone: Tone.warning,
      maskedPhone: '01712****78',
      area: 'Mirpur 10',
      itemSummary: 'Black Abaya XL',
      facts: <OrderFact>[
        OrderFact(label: 'COD', value: '৳1,250'),
        OrderFact(label: 'Courier', value: 'Steadfast'),
        OrderFact(label: 'Risk', value: 'Low', tone: Tone.good),
        OrderFact(label: 'Profit', value: '~৳485'),
      ],
    ),
    DemoOrder(
      reference: 'CP-20260909-0041',
      customerName: 'Rafi Hasan',
      statusLabel: 'Delivered',
      statusTone: Tone.good,
      maskedPhone: '01819****22',
      area: 'Uttara',
      itemSummary: 'Watch X1',
      facts: <OrderFact>[
        OrderFact(label: 'COD', value: '৳1,850'),
        OrderFact(label: 'Courier', value: 'Steadfast'),
        // Delivered but unpaid: courier state and money state are separate.
        OrderFact(label: 'Money', value: 'Pending', tone: Tone.bad),
        OrderFact(label: 'Profit', value: '৳638'),
      ],
    ),
    DemoOrder(
      reference: 'CP-20260909-0038',
      customerName: 'Mim Akter',
      statusLabel: 'Returned',
      statusTone: Tone.bad,
      maskedPhone: '01612****04',
      area: 'Savar',
      itemSummary: 'Bag A2',
      facts: <OrderFact>[
        OrderFact(label: 'Loss', value: '-৳175', tone: Tone.bad),
        OrderFact(label: 'Courier', value: 'Manual'),
        OrderFact(label: 'Reason', value: 'Refused'),
        OrderFact(label: 'Stock', value: 'Review'),
      ],
    ),
  ];

  static const List<({String label, int count})> orderFilters =
      <({String label, int count})>[
        (label: 'All', count: 214),
        (label: 'Confirmed', count: 23),
        (label: 'Packed', count: 12),
        (label: 'Booked', count: 41),
        (label: 'Transit', count: 57),
        (label: 'Delivered', count: 79),
        (label: 'Returned', count: 14),
        (label: 'COD pending', count: 61),
      ];

  // --- activity -------------------------------------------------------------

  static const List<DemoActivity> activity = <DemoActivity>[
    DemoActivity(
      author: 'Steadfast settlement',
      timestamp: '10:14 AM',
      headline: '৳12,340 reconciled',
      body:
          '11 exact consignment references matched. No unexplained deduction.',
      statusLabel: 'Matched',
      statusTone: Tone.good,
      avatarLabel: '৳',
    ),
    DemoActivity(
      author: 'Product insight',
      timestamp: 'Last 7 days',
      headline: 'Black Abaya return rate increased',
      body:
          '22% across 18 completed shipments. Review size expectation '
          'before scaling ads.',
      statusLabel: 'Watch',
      statusTone: Tone.warning,
      avatarLabel: 'P',
      avatarColor: EcomsbdColors.blue,
    ),
  ];

  // --- reconciliation -------------------------------------------------------

  static const List<DemoFinding> findings = <DemoFinding>[
    DemoFinding(
      title: 'INV-302 · exact consignment reference',
      description:
          'Expected net ৳1,405 · payout line ৳1,325. Provider '
          'deduction not mapped to a known charge type.',
      amountLabel: '-৳80',
      tags: <String>['exact ref', 'underpaid', 'unknown deduction'],
    ),
    DemoFinding(
      title: 'INV-321 · amount-only candidate',
      description:
          'Two parcels have the same amount. ecomsbd will never '
          'auto-match amount-only ambiguity.',
      amountLabel: 'Suggested',
      tone: Tone.warning,
      tags: <String>['manual review'],
    ),
  ];
}

@immutable
class DemoOrder {
  const DemoOrder({
    required this.reference,
    required this.customerName,
    required this.statusLabel,
    required this.statusTone,
    required this.facts,
    this.maskedPhone,
    this.area,
    this.itemSummary,
  });

  final String reference;
  final String customerName;
  final String statusLabel;
  final Tone statusTone;
  final List<OrderFact> facts;
  final String? maskedPhone;
  final String? area;
  final String? itemSummary;
}

@immutable
class DemoActivity {
  const DemoActivity({
    required this.author,
    required this.timestamp,
    required this.headline,
    required this.body,
    required this.statusLabel,
    required this.statusTone,
    this.avatarLabel,
    this.avatarColor,
  });

  final String author;
  final String timestamp;
  final String headline;
  final String body;
  final String statusLabel;
  final Tone statusTone;
  final String? avatarLabel;
  final Color? avatarColor;
}

@immutable
class DemoFinding {
  const DemoFinding({
    required this.title,
    required this.description,
    required this.amountLabel,
    required this.tags,
    this.tone = Tone.bad,
  });

  final String title;
  final String description;
  final String amountLabel;
  final List<String> tags;
  final Tone tone;
}
