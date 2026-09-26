import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/analytics/insights_models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';
import 'rto_screen.dart' show courierName;

/// Pieces shared by the Insights overview and its drill-down screens.
///
/// Everything here formats; nothing computes a business figure. A change is
/// shown only when the server stated one, and a withheld figure says why.

/// 7 / 30 / 90 days. One range for every Insights screen.
class InsightRangePicker extends ConsumerWidget {
  const InsightRangePicker({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final days = ref.watch(insightsDaysProvider);
    return Padding(
      padding: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      child: Wrap(
        spacing: EcomsbdSpacing.xs,
        children: <Widget>[
          for (final option in insightRanges)
            ChoiceChip(
              label: Text(
                context.tr('ins.range', <String, Object?>{'days': option}),
              ),
              selected: option == days,
              onSelected: (_) =>
                  ref.read(insightsDaysProvider.notifier).state = option,
            ),
        ],
      ),
    );
  }
}

/// "+12% vs previous", or the honest "no earlier base" when none was stated.
String changeCaption(BuildContext context, Comparison comparison) {
  final change = comparison.changeBps;
  if (change == null) return context.tr('ins.noCompare');
  return context.tr('ins.vsPrev', <String, Object?>{
    'change': changeLabel(change),
  });
}

String _bpsOrDash(Object? value) =>
    value is num ? bpsLabel(value.toInt()) : '—';

String _bucketLabel(BuildContext context, Map<String, Object?> p) {
  final min = p['min_days'];
  final max = p['max_days'];
  if (max == null) {
    return context.tr('ins.daysOpen', <String, Object?>{'min': min});
  }
  return context.tr('ins.daysRange', <String, Object?>{'min': min, 'max': max});
}

/// Words one server fact in the app's language. Unknown codes are skipped
/// rather than shown raw.
String? explanationText(BuildContext context, InsightExplanation e) {
  final p = e.params;
  final change = p['change_bps'];
  final direction = change is num && change < 0 ? 'down' : 'up';
  final vars = <String, Object?>{
    'days': p['days'],
    'change': change is num ? bpsLabel(change.toInt().abs()) : '',
    'from': _bpsOrDash(p['rto_from_bps']),
    'to': _bpsOrDash(p['rto_to_bps']),
    'share': _bpsOrDash(p['share_bps']),
    'count': p['count'],
    'missing': p['missing'],
    'parcels': p['parcels'],
    'product': p['product'],
    'courier': p['provider'] is String
        ? courierName(p['provider']! as String)
        : '',
    'amount': p['amount_paisa'] is num
        ? Money((p['amount_paisa']! as num).toInt()).format()
        : '',
    'courierAmount': p['courier_paisa'] is num
        ? Money((p['courier_paisa']! as num).toInt()).format()
        : '',
  };
  final key = switch (e.code) {
    'PROFIT_CHANGED' ||
    'PROFIT_CHANGED_WITH_RTO' ||
    'REVENUE_CHANGED' ||
    'ORDERS_CHANGED' ||
    'RECEIVED_CHANGED' => 'ins.why.${e.code}.$direction',
    'RTO_CHANGED' ||
    'OVERDUE_CONCENTRATED' ||
    'RETURN_VALUE_CONCENTRATED' ||
    'COST_DATA_INCOMPLETE' ||
    'OPEN_CASES' ||
    'SLOW_MOVING' ||
    'OUT_OF_STOCK' ||
    'LOW_STOCK' ||
    'NEW_CUSTOMERS' ||
    'REPEAT_ORDER_SHARE' => 'ins.why.${e.code}',
    'RECEIVABLE_AGE_SHARE' => 'ins.why.RECEIVABLE_AGE_SHARE',
    _ => null,
  };
  if (key == null) return null;
  if (e.code == 'RECEIVABLE_AGE_SHARE') {
    vars['bucket'] = _bucketLabel(context, p);
  }
  return context.tr(key, vars);
}

/// "What changed": the server's facts, worded, in the server's order.
class WhatChangedCard extends StatelessWidget {
  const WhatChangedCard({required this.explanations, super.key});

  final List<InsightExplanation> explanations;

  @override
  Widget build(BuildContext context) {
    final lines = <String>[
      for (final e in explanations)
        if (explanationText(context, e) case final line?) line,
    ];
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Text(context.tr('ins.whatChanged'), style: EcomsbdType.sectionTitle),
          const SizedBox(height: 2),
          Text(
            context.tr('ins.whatChangedSub'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          if (lines.isEmpty)
            Text(
              context.tr('ins.nothingChanged'),
              style: EcomsbdType.body.copyWith(color: EcomsbdColors.muted),
            ),
          for (final line in lines)
            Padding(
              padding: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Padding(
                    padding: EdgeInsets.only(top: 7, right: 8),
                    child: Icon(
                      Icons.circle,
                      size: 6,
                      color: EcomsbdColors.muted,
                    ),
                  ),
                  Expanded(child: Text(line, style: EcomsbdType.body)),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Why money is missing: the role cannot see it, or the plan does not cover
/// this range. Never a row of zeroes.
class MoneyLockedNotice extends StatelessWidget {
  const MoneyLockedNotice({required this.reason, super.key});

  final String reason;

  @override
  Widget build(BuildContext context) {
    if (reason == 'PLAN') {
      return const GlassCard(child: PlanLockedNotice());
    }
    return GlassCard(
      child: Row(
        children: <Widget>[
          const Icon(
            Icons.lock_outline_rounded,
            size: 18,
            color: EcomsbdColors.muted,
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              context.tr('ins.moneyLockedRole'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
      ),
    );
  }
}

/// One labelled value in a scorecard or detail card.
class FactRow extends StatelessWidget {
  const FactRow({
    required this.label,
    required this.value,
    super.key,
    this.tone,
  });

  final String label;
  final String value;
  final Tone? tone;

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
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.end,
              style: EcomsbdType.bodyStrong.copyWith(color: tone?.ink),
            ),
          ),
        ],
      ),
    );
  }
}

/// A zero-based bar chart: bars grow from a zero line, so a small day never
/// looks like a large one and a loss reads as below the line.
///
/// A [CustomPainter] with no blur or clipping layers, cheap on low-end Android.
class InsightBarChart extends StatelessWidget {
  const InsightBarChart({
    required this.values,
    required this.semanticsLabel,
    super.key,
    this.firstLabel,
    this.lastLabel,
    this.height = 140,
  });

  final List<int> values;
  final String semanticsLabel;
  final String? firstLabel;
  final String? lastLabel;
  final double height;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: semanticsLabel,
      excludeSemantics: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          SizedBox(
            height: height,
            child: RepaintBoundary(
              child: CustomPaint(painter: _BarPainter(values)),
            ),
          ),
          const SizedBox(height: 4),
          Row(
            children: <Widget>[
              Text(firstLabel ?? '', style: _axis),
              const Spacer(),
              Text(lastLabel ?? '', style: _axis),
            ],
          ),
        ],
      ),
    );
  }

  static final TextStyle _axis = EcomsbdType.caption.copyWith(
    color: EcomsbdColors.chartLabel,
    fontSize: 10,
  );
}

class _BarPainter extends CustomPainter {
  _BarPainter(this.values);

  final List<int> values;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.isEmpty) return;
    final top = values.fold<int>(0, (a, b) => b > a ? b : a);
    final bottom = values.fold<int>(0, (a, b) => b < a ? b : a);
    final span = (top - bottom) == 0 ? 1 : top - bottom;
    final zeroY = size.height * top / span;
    final slot = size.width / values.length;
    final barWidth = (slot * 0.64).clamp(1.0, 18.0);

    canvas.drawLine(
      Offset(0, zeroY),
      Offset(size.width, zeroY),
      Paint()
        ..color = EcomsbdColors.stroke
        ..strokeWidth = 1,
    );
    final up = Paint()..color = EcomsbdColors.blue;
    final down = Paint()..color = EcomsbdColors.red;
    for (var i = 0; i < values.length; i++) {
      final value = values[i];
      if (value == 0) continue;
      final length = size.height * value.abs() / span;
      final left = i * slot + (slot - barWidth) / 2;
      final rect = value > 0
          ? Rect.fromLTWH(left, zeroY - length, barWidth, length)
          : Rect.fromLTWH(left, zeroY, barWidth, length);
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(2)),
        value > 0 ? up : down,
      );
    }
  }

  @override
  bool shouldRepaint(_BarPainter old) => old.values != values;
}

/// The frame every Insights drill-down shares: back, header, range, refresh.
class InsightDetailScaffold extends StatelessWidget {
  const InsightDetailScaffold({
    required this.title,
    required this.description,
    required this.onRefresh,
    required this.children,
    super.key,
    this.showRange = true,
  });

  final String title;
  final String description;
  final Future<void> Function() onRefresh;
  final List<Widget> children;
  final bool showRange;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: onRefresh,
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
                          eyebrow: context.tr('insights.eyebrow'),
                          title: title,
                          description: description,
                        ),
                      ),
                    ],
                  ),
                  if (showRange) const InsightRangePicker(),
                  ...children,
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Loading, error and stale states for one `Sourced` section.
List<Widget> sourcedSection<T>(
  BuildContext context,
  AsyncValue<dynamic> value, {
  required List<Widget> Function(T value) data,
  required VoidCallback onRetry,
}) {
  return value.when(
    loading: () => <Widget>[SkeletonLoader.card(height: 180)],
    error: (error, _) => <Widget>[
      if (error is ApiError && error.isPlanLimited)
        const GlassCard(child: PlanLockedNotice())
      else if (error is ApiError)
        ErrorStateCard(error: error, onRetry: onRetry)
      else
        EmptyState(
          icon: Icons.error_outline,
          title: context.tr('ins.couldNotLoad'),
          message: context.tr('common.somethingWentWrong'),
        ),
    ],
    data: (sourced) => <Widget>[
      if (sourced.isStale as bool) ...<Widget>[
        StaleDataNotice(
          fetchedAt: sourced.fetchedAt as DateTime?,
          onRetry: onRetry,
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      ...data(sourced.value as T),
    ],
  );
}

/// A muted one-line note.
class InsightNote extends StatelessWidget {
  const InsightNote(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: EcomsbdSpacing.xs),
      child: Text(
        text,
        style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
      ),
    );
  }
}

/// `dd/MM`, for chart axis ends.
String shortDate(DateTime? date) {
  if (date == null) return '';
  String two(int v) => v.toString().padLeft(2, '0');
  return '${two(date.day)}/${two(date.month)}';
}
