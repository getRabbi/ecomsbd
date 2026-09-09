import 'package:flutter/material.dart';

import '../tokens.dart';

/// One stage of the delivery funnel.
class FunnelStage {
  const FunnelStage({
    required this.label,
    required this.count,
    this.colors = const <Color>[EcomsbdColors.navyLight, EcomsbdColors.blue],
  });

  final String label;
  final int count;
  final List<Color> colors;
}

/// `.funnel` — booked → picked up → delivered → returned → unresolved.
///
/// Bars are scaled against the *first* stage rather than the largest, so the
/// funnel reads as attrition from the top rather than as an unrelated ranking.
class DeliveryFunnelChart extends StatelessWidget {
  const DeliveryFunnelChart({required this.stages, super.key});

  final List<FunnelStage> stages;

  @override
  Widget build(BuildContext context) {
    if (stages.isEmpty) {
      return const SizedBox.shrink();
    }
    final baseline = stages.first.count == 0 ? 1 : stages.first.count;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        for (final stage in stages)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Semantics(
              label: '${stage.label}: ${stage.count}',
              excludeSemantics: true,
              child: Row(
                children: <Widget>[
                  SizedBox(
                    width: 74,
                    child: Text(
                      stage.label,
                      style: EcomsbdType.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Expanded(
                    child: _Track(
                      fraction: stage.count / baseline,
                      colors: stage.colors,
                    ),
                  ),
                  const SizedBox(width: EcomsbdSpacing.xs),
                  SizedBox(
                    width: 44,
                    child: Text(
                      '${stage.count}',
                      style: EcomsbdType.label.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                      textAlign: TextAlign.right,
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

/// One labelled horizontal bar, used for product profitability and return
/// pressure.
class HorizontalBarDatum {
  const HorizontalBarDatum({
    required this.label,
    required this.value,
    required this.displayValue,
    this.colors,
  });

  final String label;

  /// Magnitude used for the bar length.
  final num value;

  /// Pre-formatted trailing text (money or a percentage).
  final String displayValue;

  final List<Color>? colors;
}

/// `.hbar` — a ranked horizontal bar list.
///
/// Bars are relative to the largest item in the set, which is what makes "Red
/// Abaya is roughly twice Watch X1" readable at a glance.
class HorizontalBarChart extends StatelessWidget {
  const HorizontalBarChart({
    required this.data,
    super.key,
    this.accent = BarAccent.orange,
    this.labelWidth = 84,
  });

  final List<HorizontalBarDatum> data;
  final BarAccent accent;
  final double labelWidth;

  @override
  Widget build(BuildContext context) {
    if (data.isEmpty) {
      return const SizedBox.shrink();
    }
    final maxValue = data
        .map((d) => d.value.abs())
        .reduce((a, b) => a > b ? a : b);
    final safeMax = maxValue == 0 ? 1 : maxValue;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        for (final datum in data)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Semantics(
              label: '${datum.label}: ${datum.displayValue}',
              excludeSemantics: true,
              child: Row(
                children: <Widget>[
                  SizedBox(
                    width: labelWidth,
                    child: Text(
                      datum.label,
                      style: EcomsbdType.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  Expanded(
                    child: _Track(
                      fraction: datum.value.abs() / safeMax,
                      colors: datum.colors ?? accent.colors,
                    ),
                  ),
                  const SizedBox(width: EcomsbdSpacing.xs),
                  SizedBox(
                    width: 58,
                    child: Text(
                      datum.displayValue,
                      style: EcomsbdType.label.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                      textAlign: TextAlign.right,
                    ),
                  ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

enum BarAccent { orange, green, red, blue }

extension BarAccentColors on BarAccent {
  List<Color> get colors => switch (this) {
    BarAccent.orange => const <Color>[
      EcomsbdColors.orangeLight,
      EcomsbdColors.orange,
    ],
    BarAccent.green => const <Color>[Color(0xFF57B37D), EcomsbdColors.green],
    BarAccent.red => const <Color>[Color(0xFFE46F74), EcomsbdColors.red],
    BarAccent.blue => const <Color>[
      EcomsbdColors.navyLight,
      EcomsbdColors.blue,
    ],
  };
}

class _Track extends StatelessWidget {
  const _Track({required this.fraction, required this.colors});

  final double fraction;
  final List<Color> colors;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: EcomsbdRadii.round,
      child: SizedBox(
        height: 9,
        child: Stack(
          children: <Widget>[
            const ColoredBox(
              color: EcomsbdColors.trackLight,
              child: SizedBox.expand(),
            ),
            FractionallySizedBox(
              widthFactor: fraction.isFinite ? fraction.clamp(0.0, 1.0) : 0.0,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: EcomsbdRadii.round,
                  gradient: LinearGradient(colors: colors),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// A day in the settled-vs-due comparison.
class ComparisonBar {
  const ComparisonBar({
    required this.label,
    required this.expected,
    required this.received,
  });

  final String label;

  /// Paisa expected on this settlement day.
  final int expected;

  /// Paisa actually received.
  final int received;
}

/// The grouped bar chart from the Money screen: expected behind, received in
/// front.
///
/// Overlaying rather than pairing side by side makes the shortfall visible as a
/// sliver of the lighter bar, which is the thing the seller needs to notice.
class SettledVsDueChart extends StatelessWidget {
  const SettledVsDueChart({required this.bars, super.key, this.height = 150});

  final List<ComparisonBar> bars;
  final double height;

  @override
  Widget build(BuildContext context) {
    if (bars.isEmpty) {
      return SizedBox(height: height);
    }
    return Semantics(
      label: 'Expected versus received settlement over ${bars.length} days',
      excludeSemantics: true,
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: CustomPaint(
          painter: _ComparisonPainter(
            bars: bars,
            labelStyle: EcomsbdType.caption.copyWith(
              color: EcomsbdColors.chartLabel,
              fontSize: 10,
            ),
          ),
        ),
      ),
    );
  }
}

class _ComparisonPainter extends CustomPainter {
  _ComparisonPainter({required this.bars, required this.labelStyle});

  final List<ComparisonBar> bars;
  final TextStyle labelStyle;

  @override
  void paint(Canvas canvas, Size size) {
    const labelHeight = 18.0;
    final plot = Rect.fromLTWH(
      4,
      6,
      size.width - 8,
      size.height - labelHeight - 10,
    );

    final grid = Paint()
      ..color = EcomsbdColors.gridline
      ..strokeWidth = 1;
    for (var i = 0; i <= 3; i++) {
      final y = plot.top + plot.height * (i / 3);
      canvas.drawLine(Offset(plot.left, y), Offset(plot.right, y), grid);
    }

    final maxValue = bars
        .map((b) => b.expected > b.received ? b.expected : b.received)
        .reduce((a, b) => a > b ? a : b);
    final safeMax = maxValue == 0 ? 1 : maxValue;

    final slot = plot.width / bars.length;
    final barWidth = (slot * 0.46).clamp(6.0, 26.0);

    for (var i = 0; i < bars.length; i++) {
      final bar = bars[i];
      final centerX = plot.left + slot * (i + 0.5);

      _drawBar(
        canvas,
        centerX: centerX,
        width: barWidth,
        plot: plot,
        fraction: bar.expected / safeMax,
        color: EcomsbdColors.barComparisonBase,
      );
      _drawBar(
        canvas,
        centerX: centerX,
        width: barWidth * 0.68,
        plot: plot,
        fraction: bar.received / safeMax,
        color: EcomsbdColors.orange,
      );

      final painter = TextPainter(
        text: TextSpan(text: bar.label, style: labelStyle),
        textDirection: TextDirection.ltr,
      )..layout();
      final x = (centerX - painter.width / 2).clamp(
        0.0,
        size.width - painter.width,
      );
      painter.paint(canvas, Offset(x, size.height - painter.height));
    }
  }

  void _drawBar(
    Canvas canvas, {
    required double centerX,
    required double width,
    required Rect plot,
    required double fraction,
    required Color color,
  }) {
    final barHeight = plot.height * fraction.clamp(0.0, 1.0);
    final rect = Rect.fromLTWH(
      centerX - width / 2,
      plot.bottom - barHeight,
      width,
      barHeight,
    );
    canvas.drawRRect(
      RRect.fromRectAndCorners(
        rect,
        topLeft: const Radius.circular(5),
        topRight: const Radius.circular(5),
      ),
      Paint()..color = color,
    );
  }

  @override
  bool shouldRepaint(_ComparisonPainter oldDelegate) =>
      oldDelegate.bars != bars;
}
