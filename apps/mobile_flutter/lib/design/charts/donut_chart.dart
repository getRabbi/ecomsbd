import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../tokens.dart';

/// One wedge of the COD composition donut.
class DonutSlice {
  const DonutSlice({
    required this.label,
    required this.value,
    required this.color,
  });

  final String label;

  /// Paisa, or any consistent integer unit. Shares are computed from the total.
  final int value;

  final Color color;
}

/// `.donut` — the COD position chart with a centred total and a legend.
///
/// The legend prints each slice's label and percentage, so the split is
/// readable without relying on colour (master spec section 122).
class CodDonutChart extends StatelessWidget {
  const CodDonutChart({
    required this.slices,
    required this.centerValue,
    super.key,
    this.centerLabel = 'outstanding',
    this.diameter = 132,
    this.showLegend = true,
  });

  final List<DonutSlice> slices;

  /// Pre-formatted, because the total belongs to the caller's money model.
  final String centerValue;

  final String centerLabel;
  final double diameter;
  final bool showLegend;

  int get _total => slices.fold(0, (sum, slice) => sum + slice.value);

  @override
  Widget build(BuildContext context) {
    final total = _total;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        SizedBox(
          height: diameter,
          width: diameter,
          child: Stack(
            alignment: Alignment.center,
            children: <Widget>[
              CustomPaint(
                size: Size.square(diameter),
                painter: _DonutPainter(slices: slices, total: total),
              ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Text(centerValue, style: EcomsbdType.metricValue),
                  Text(
                    centerLabel,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        if (showLegend) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
          _Legend(slices: slices, total: total),
        ],
      ],
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend({required this.slices, required this.total});

  final List<DonutSlice> slices;
  final int total;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      spacing: EcomsbdSpacing.md,
      runSpacing: EcomsbdSpacing.xs,
      children: <Widget>[
        for (final slice in slices)
          Row(
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: slice.color,
                ),
              ),
              const SizedBox(width: 5),
              Text(
                total == 0
                    ? slice.label
                    : '${slice.label} · ${(slice.value * 100 / total).round()}%',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ),
      ],
    );
  }
}

class _DonutPainter extends CustomPainter {
  _DonutPainter({required this.slices, required this.total});

  final List<DonutSlice> slices;
  final int total;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final strokeWidth = size.width * 0.155;
    final radius = (size.width - strokeWidth) / 2;
    final center = rect.center;

    final track = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = strokeWidth
      ..color = EcomsbdColors.donutRemainder;
    canvas.drawCircle(center, radius, track);

    if (total <= 0) {
      return;
    }

    var start = -math.pi / 2;
    // A hairline gap between wedges keeps adjacent slices distinguishable
    // without a border, which would read as a fifth colour.
    const gap = 0.012;

    for (final slice in slices) {
      if (slice.value <= 0) {
        continue;
      }
      final sweep = 2 * math.pi * (slice.value / total);
      canvas.drawArc(
        Rect.fromCircle(center: center, radius: radius),
        start + gap / 2,
        math.max(0, sweep - gap),
        false,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = strokeWidth
          ..strokeCap = StrokeCap.butt
          ..color = slice.color,
      );
      start += sweep;
    }
  }

  @override
  bool shouldRepaint(_DonutPainter oldDelegate) =>
      oldDelegate.slices != slices || oldDelegate.total != total;
}
