import 'package:flutter/material.dart';

import '../glass.dart';
import '../tokens.dart';
import '../../widgets/brand/ecoms_brand_mark.dart';

/// `.glass-pill` — the floating capsule the top bar is built from.
class GlassTopPill extends StatelessWidget {
  const GlassTopPill({
    required this.child,
    super.key,
    this.onTap,
    this.padding = const EdgeInsets.symmetric(horizontal: 4),
    this.minHeight = EcomsbdTouch.minTarget,
  });

  final Widget child;
  final VoidCallback? onTap;
  final EdgeInsetsGeometry padding;
  final double minHeight;

  @override
  Widget build(BuildContext context) {
    final content = ConstrainedBox(
      constraints: BoxConstraints(minHeight: minHeight),
      child: Padding(
        padding: padding,
        child: Center(widthFactor: 1, child: child),
      ),
    );

    return GlassSurface(
      borderRadius: EcomsbdRadii.round,
      fill: const Color(0xB8FAFCFD),
      shadows: EcomsbdShadows.pill,
      blurSigma: 22,
      child: onTap == null
          ? content
          : Material(
              color: Colors.transparent,
              child: InkWell(
                onTap: onTap,
                borderRadius: EcomsbdRadii.round,
                child: content,
              ),
            ),
    );
  }
}

/// The product identity pill: the brand mark plus the `ecomsbd` wordmark, or
/// the current section's title on a main tab.
///
/// A label, not a control. It used to carry a dropdown chevron and open the
/// menu when tapped, which read as a section switcher that was not there; the
/// hamburger is the one way into the menu, so this pill takes no tap and draws
/// no chevron.
class BrandPill extends StatelessWidget {
  const BrandPill({super.key, this.label = 'ecomsbd'});

  /// Displayed text. Defaults to the locked product name.
  final String label;

  @override
  Widget build(BuildContext context) {
    return GlassTopPill(
      padding: const EdgeInsets.symmetric(horizontal: 12),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          // The same 30dp slot the placeholder round mark used, so the pill
          // keeps its size on every screen. The mark hops now and then; the
          // hop is paint-only and stays inside the pill.
          const EcomsBrandMark(size: 30, animate: true),
          const SizedBox(width: EcomsbdSpacing.sm),
          Flexible(
            child: Semantics(
              header: true,
              child: Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: EcomsbdType.bodyStrong.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// `.icon-btn` — a circular action inside an action pill.
class GlassIconButton extends StatelessWidget {
  const GlassIconButton({
    required this.icon,
    required this.tooltip,
    super.key,
    this.onPressed,
  });

  final IconData icon;

  /// Doubles as the accessibility label, so every icon-only control is named
  /// (master spec section 124).
  final String tooltip;

  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: IconButton(
        onPressed: onPressed,
        icon: Icon(icon, size: 21),
        color: EcomsbdColors.ink,
        splashRadius: 22,
        constraints: const BoxConstraints(
          minWidth: EcomsbdTouch.minTarget,
          minHeight: EcomsbdTouch.minTarget,
        ),
        padding: EdgeInsets.zero,
      ),
    );
  }
}

/// The top bar: brand pill on the left, action pill on the right.
class GlassTopBar extends StatelessWidget {
  const GlassTopBar({required this.leading, required this.actions, super.key});

  final Widget leading;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.md,
        EcomsbdSpacing.sm,
        EcomsbdSpacing.md,
        EcomsbdSpacing.md,
      ),
      child: Row(
        children: <Widget>[
          // Expanded, not Flexible + Spacer: those split the free space in
          // half, which cut the brand pill's label off on a 360dp phone.
          Expanded(
            child: Align(alignment: Alignment.centerLeft, child: leading),
          ),
          const SizedBox(width: EcomsbdSpacing.sm),
          if (actions.isNotEmpty)
            GlassTopPill(
              child: Row(mainAxisSize: MainAxisSize.min, children: actions),
            ),
        ],
      ),
    );
  }
}

/// `.back-pill` — the circular back control on detail screens.
class GlassBackPill extends StatelessWidget {
  const GlassBackPill({required this.onPressed, super.key});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return GlassTopPill(
      onTap: onPressed,
      padding: EdgeInsets.zero,
      child: const SizedBox(
        width: EcomsbdTouch.minTarget,
        child: Icon(Icons.arrow_back_rounded, size: 21),
      ),
    );
  }
}
