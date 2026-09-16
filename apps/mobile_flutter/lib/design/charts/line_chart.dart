import 'package:flutter/material.dart';

import '../tokens.dart';
import '../../l10n/app_strings.dart';

/// One point on the profit trend.
class TrendPoint {
  const TrendPoint({required this.label, required this.value});

  /// Axis label, e.g. `Thu`.
  final String label;

  /// Paisa. Kept as an integer end to end so the chart cannot disagree with the
  /// figure printed above it.
  final int value;
}

/// The smooth contribution-profit trend from the prototype: a gradient stroke
/// with a fading area fill and emphasised vertices.
///
/// Implemented as a [CustomPainter] rather than with a charting package. The
/// prototype's exact gradient, cap style and area fade would need overriding at
/// every level of a generic library, and the app avoids a dependency it would
/// mostly be fighting (see docs/ADR/0002-no-chart-package.md).
class ProfitTrendChart extends StatelessWidget {
  const ProfitTrendChart({
    required this.points,
    super.key,
    this.height = 150,
    this.showLabels = true,
  });

  final List<TrendPoint> points;
  final double height;
  final bool showLabels;

  @override
  Widget build(BuildContext context) {
    if (points.length < 2) {
      return SizedBox(
        height: height,
        child: Center(
          child: Text(
            context.tr('chart.notEnoughHistory'),
            style: const TextStyle(color: EcomsbdColors.muted),
          ),
        ),
      );
    }

    return Semantics(
      label: 'Contribution profit trend over ${points.length} days',
      excludeSemantics: true,
      child: SizedBox(
        height: height,
        width: double.infinity,
        child: CustomPaint(
          painter: _ProfitTrendPainter(
            points: points,
            showLabels: showLabels,
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

class _ProfitTrendPainter extends CustomPainter {
  _ProfitTrendPainter({
    required this.points,
    required this.showLabels,
    required this.labelStyle,
  });

  final List<TrendPoint> points;
  final bool showLabels;
  final TextStyle labelStyle;

  static const double _gridLines = 4;

  @override
  void paint(Canvas canvas, Size size) {
    final labelHeight = showLabels ? 18.0 : 0.0;
    final plot = Rect.fromLTWH(
      6,
      8,
      size.width - 12,
      size.height - labelHeight - 16,
    );

    _paintGrid(canvas, plot);

    final values = points.map((p) => p.value).toList(growable: false);
    final maxValue = values.reduce((a, b) => a > b ? a : b);
    final minValue = values.reduce((a, b) => a < b ? a : b);
    // A flat series must not divide by zero, and a series that never touches
    // the floor reads better with a little headroom.
    final span = (maxValue - minValue) == 0 ? 1 : (maxValue - minValue);
    final headroom = span * 0.18;

    final offsets = <Offset>[
      for (var i = 0; i < points.length; i++)
        Offset(
          plot.left + plot.width * (i / (points.length - 1)),
          plot.bottom -
              plot.height *
                  ((points[i].value - minValue + headroom * 0.5) /
                      (span + headroom)),
        ),
    ];

    final line = _smoothPath(offsets);

    final area = Path.from(line)
      ..lineTo(offsets.last.dx, plot.bottom)
      ..lineTo(offsets.first.dx, plot.bottom)
      ..close();

    canvas.drawPath(
      area,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: <Color>[Color(0x2EFF4500), Color(0x00FF4500)],
        ).createShader(plot),
    );

    canvas.drawPath(
      line,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4
        ..strokeCap = StrokeCap.round
        ..strokeJoin = StrokeJoin.round
        ..shader = const LinearGradient(
          colors: <Color>[EcomsbdColors.blue, EcomsbdColors.orange],
        ).createShader(plot),
    );

    _paintVertices(canvas, offsets);

    if (showLabels) {
      _paintLabels(canvas, size, plot, offsets);
    }
  }

  void _paintGrid(Canvas canvas, Rect plot) {
    final paint = Paint()
      ..color = EcomsbdColors.gridline
      ..strokeWidth = 1;
    for (var i = 0; i <= _gridLines; i++) {
      final y = plot.top + plot.height * (i / _gridLines);
      canvas.drawLine(Offset(plot.left, y), Offset(plot.right, y), paint);
    }
  }

  /// Catmull-Rom smoothing expressed as cubic Béziers.
  ///
  /// A polyline reads as noise at this size; a spline shows the trend, which is
  /// what the card is for. Tangents are clamped to the segment so the curve
  /// cannot overshoot and imply a value the data never reached.
  Path _smoothPath(List<Offset> pts) {
    final path = Path()..moveTo(pts.first.dx, pts.first.dy);
    for (var i = 0; i < pts.length - 1; i++) {
      final p0 = i == 0 ? pts[i] : pts[i - 1];
      final p1 = pts[i];
      final p2 = pts[i + 1];
      final p3 = i + 2 < pts.length ? pts[i + 2] : p2;

      final c1 = Offset(
        p1.dx + (p2.dx - p0.dx) / 6,
        p1.dy + (p2.dy - p0.dy) / 6,
      );
      final c2 = Offset(
        p2.dx - (p3.dx - p1.dx) / 6,
        p2.dy - (p3.dy - p1.dy) / 6,
      );
      path.cubicTo(c1.dx, c1.dy, c2.dx, c2.dy, p2.dx, p2.dy);
    }
    return path;
  }

  void _paintVertices(Canvas canvas, List<Offset> offsets) {
    final fill = Paint()..color = Colors.white;
    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 3
      ..color = EcomsbdColors.orange;

    // Every point on a dense series would be clutter; mark a handful evenly.
    final step = offsets.length <= 5 ? 1 : (offsets.length / 4).ceil();
    for (var i = 0; i < offsets.length; i += step) {
      canvas.drawCircle(offsets[i], 4, fill);
      canvas.drawCircle(offsets[i], 4, stroke);
    }
    canvas.drawCircle(offsets.last, 4, fill);
    canvas.drawCircle(offsets.last, 4, stroke);
  }

  void _paintLabels(Canvas canvas, Size size, Rect plot, List<Offset> offsets) {
    // Thin the labels until they fit, rather than letting them overlap.
    const approxLabelWidth = 30.0;
    final maxLabels = (plot.width / approxLabelWidth).floor().clamp(2, 8);
    final step = (points.length / maxLabels).ceil();

    for (var i = 0; i < points.length; i += step) {
      final painter = TextPainter(
        text: TextSpan(text: points[i].label, style: labelStyle),
        textDirection: TextDirection.ltr,
      )..layout();
      final x = (offsets[i].dx - painter.width / 2).clamp(
        0.0,
        size.width - painter.width,
      );
      painter.paint(canvas, Offset(x, size.height - painter.height));
    }
  }

  @override
  bool shouldRepaint(_ProfitTrendPainter oldDelegate) =>
      oldDelegate.points != points || oldDelegate.showLabels != showLabels;
}
