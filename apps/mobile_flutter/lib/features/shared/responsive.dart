import 'package:flutter/material.dart';

/// A grid that chooses its column count from the available width.
///
/// The prototype uses CSS media queries at 620px; a Flutter app faces every
/// width from a 320dp phone to a tablet, so the count is derived from a minimum
/// tile width instead of a fixed breakpoint. That keeps the 360dp Android case
/// — the primary target (master spec section 53) — from ever squeezing four
/// tiles into a row where each is 70dp wide.
class ResponsiveGrid extends StatelessWidget {
  const ResponsiveGrid({
    required this.children,
    super.key,
    this.minTileWidth = 160,
    this.maxColumns = 4,
    this.spacing = 8,
    this.runSpacing,
  });

  final List<Widget> children;

  /// Narrowest a tile may be before the grid drops a column.
  final double minTileWidth;

  final int maxColumns;
  final double spacing;
  final double? runSpacing;

  @override
  Widget build(BuildContext context) {
    if (children.isEmpty) {
      return const SizedBox.shrink();
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final available = constraints.maxWidth;
        var columns = ((available + spacing) / (minTileWidth + spacing))
            .floor()
            .clamp(1, maxColumns);
        columns = columns.clamp(1, children.length);

        final tileWidth = (available - spacing * (columns - 1)) / columns;

        // Tiles in a row share the tallest one's height. A Bangla title that
        // wraps to two lines would otherwise leave its neighbour visibly
        // short, which reads as a broken card rather than a longer word.
        final rows = <Widget>[];
        for (var start = 0; start < children.length; start += columns) {
          final end = start + columns < children.length
              ? start + columns
              : children.length;
          rows.add(
            IntrinsicHeight(
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  for (var i = start; i < end; i++) ...<Widget>[
                    if (i > start) SizedBox(width: spacing),
                    SizedBox(width: tileWidth, child: children[i]),
                  ],
                ],
              ),
            ),
          );
        }

        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            for (var i = 0; i < rows.length; i++) ...<Widget>[
              if (i > 0) SizedBox(height: runSpacing ?? spacing),
              rows[i],
            ],
          ],
        );
      },
    );
  }
}

/// Layout breakpoints.
class Breakpoints {
  const Breakpoints._();

  /// Below this the layout is single-column. Covers the 360dp reference device.
  static const double compact = 620;

  static bool isCompact(BuildContext context) =>
      MediaQuery.sizeOf(context).width < compact;
}

/// Constrains content on wide screens.
///
/// The prototype caps its shell at 780px; without this, a tablet would stretch
/// a seller-operations layout into unreadably long lines.
class ContentWidthLimit extends StatelessWidget {
  const ContentWidthLimit({
    required this.child,
    super.key,
    this.maxWidth = 780,
  });

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: ConstrainedBox(
        constraints: BoxConstraints(maxWidth: maxWidth),
        child: child,
      ),
    );
  }
}
