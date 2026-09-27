import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../glass.dart';
import '../theme.dart';
import '../tokens.dart';

/// The page shell every screen sits in.
///
/// Owns the gradient background, the status-bar style, the safe area and the
/// clearance beneath the floating bottom navigation. Screens supply content;
/// they never repeat this scaffolding.
class EcomsbdScaffold extends StatelessWidget {
  const EcomsbdScaffold({
    required this.child,
    super.key,
    this.topBar,
    this.bottomBar,
    this.overlayStyle = ecomsbdLightOverlay,
    this.extendBehindTopBar = false,
    this.backgroundOverride,
  });

  final Widget child;

  /// Floating glass pill row pinned to the top.
  final Widget? topBar;

  /// Usually [BottomGlassNavigation].
  final Widget? bottomBar;

  final SystemUiOverlayStyle overlayStyle;

  /// True on screens whose hero runs under the top bar (Home).
  final bool extendBehindTopBar;

  final Widget? backgroundOverride;

  @override
  Widget build(BuildContext context) {
    return AnnotatedRegion<SystemUiOverlayStyle>(
      value: overlayStyle,
      child: Scaffold(
        backgroundColor: EcomsbdColors.background,
        // The design is a single scrolling surface; a Material AppBar would
        // fight the floating glass pills.
        body: EcomsbdBackground(
          child: Stack(
            children: <Widget>[
              if (backgroundOverride != null)
                Positioned.fill(child: backgroundOverride!),
              Positioned.fill(
                child: extendBehindTopBar
                    ? child
                    : SafeArea(bottom: false, child: child),
              ),
              // `.topbar`: a frosted band the width of the screen, so a page
              // scrolled under the pills reads as passing behind the header
              // rather than showing between them.
              if (topBar != null)
                Positioned(
                  left: 0,
                  right: 0,
                  top: 0,
                  child: GlassSurface(
                    borderRadius: BorderRadius.zero,
                    fill: const Color(0xC7F4F7FA),
                    borderColor: null,
                    shadows: const <BoxShadow>[],
                    blurSigma: 22,
                    child: DecoratedBox(
                      decoration: const BoxDecoration(
                        border: Border(
                          bottom: BorderSide(color: Color(0x8CFFFFFF)),
                        ),
                      ),
                      child: SafeArea(bottom: false, child: topBar!),
                    ),
                  ),
                ),
              // Hidden while the keyboard is up: the body resizes, so the
              // floating bar would otherwise ride up and cover the field
              // being typed into.
              if (bottomBar != null &&
                  MediaQuery.viewInsetsOf(context).bottom == 0)
                Positioned(left: 0, right: 0, bottom: 0, child: bottomBar!),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.card.flat` / `.list-card` — the standard card: white, a hairline border,
/// no shadow. The final prototype draws almost every surface this way.
class GlassCard extends StatelessWidget {
  const GlassCard({
    required this.child,
    super.key,
    this.padding = const EdgeInsets.all(EcomsbdSpacing.md),
    this.margin,
    this.onTap,
    this.borderRadius = EcomsbdRadii.card,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;
  final VoidCallback? onTap;
  final BorderRadius borderRadius;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      margin: margin,
      borderRadius: borderRadius,
      fill: Colors.white,
      borderColor: EcomsbdColors.line,
      shadows: const <BoxShadow>[],
      // This is the card that appears a dozen times in a scrolling list, so
      // it takes the thickened fill rather than a live blur. See GlassSurface.
      blurSigma: 0,
      child: _Tappable(
        onTap: onTap,
        borderRadius: borderRadius,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}

/// `.card` — the white card with a restrained shadow, for a panel that leads
/// its section (a money summary, a detected order).
class StrongGlassCard extends StatelessWidget {
  const StrongGlassCard({
    required this.child,
    super.key,
    this.padding = const EdgeInsets.all(EcomsbdSpacing.lg),
    this.margin,
    this.onTap,
    this.borderRadius = EcomsbdRadii.cardLarge,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final EdgeInsetsGeometry? margin;
  final VoidCallback? onTap;
  final BorderRadius borderRadius;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: Colors.white,
      borderColor: EcomsbdColors.line,
      shadows: EcomsbdShadows.card,
      margin: margin,
      borderRadius: borderRadius,
      // Already near-opaque at 0xE0, so the live blur behind it was paying for
      // almost nothing. See GlassSurface.
      blurSigma: 0,
      child: _Tappable(
        onTap: onTap,
        borderRadius: borderRadius,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}

class _Tappable extends StatelessWidget {
  const _Tappable({
    required this.child,
    required this.borderRadius,
    this.onTap,
  });

  final Widget child;
  final BorderRadius borderRadius;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    if (onTap == null) {
      return child;
    }
    return Material(
      color: Colors.transparent,
      child: InkWell(onTap: onTap, borderRadius: borderRadius, child: child),
    );
  }
}

/// `.section-title` — a heading with optional subtitle and trailing action.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    required this.title,
    super.key,
    this.subtitle,
    this.actionLabel,
    this.onAction,
  });

  final String title;
  final String? subtitle;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    // `.section-head`: a strong title, a quiet line under it, and an orange
    // text link level with the subtitle's baseline.
    return Padding(
      padding: const EdgeInsets.fromLTRB(3, 18, 3, EcomsbdSpacing.sm),
      child: Row(
        crossAxisAlignment: subtitle == null
            ? CrossAxisAlignment.center
            : CrossAxisAlignment.end,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(title, style: EcomsbdType.sectionHead),
                if (subtitle != null) ...<Widget>[
                  const SizedBox(height: 3),
                  Text(
                    subtitle!,
                    style: EcomsbdType.body.copyWith(
                      color: EcomsbdColors.muted,
                      height: 1.35,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (actionLabel != null && onAction != null)
            TextButton(
              onPressed: onAction,
              style: TextButton.styleFrom(
                minimumSize: const Size(0, 40),
                padding: const EdgeInsets.symmetric(horizontal: 8),
                foregroundColor: EcomsbdColors.orange,
                textStyle: EcomsbdType.label.copyWith(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                ),
              ),
              child: Text(actionLabel!),
            ),
        ],
      ),
    );
  }
}

/// `.page-header` — eyebrow, title and supporting line for non-Home screens.
class PageHeader extends StatelessWidget {
  const PageHeader({
    required this.title,
    super.key,
    this.eyebrow,
    this.description,
  });

  final String title;
  final String? eyebrow;
  final String? description;

  @override
  Widget build(BuildContext context) {
    // `.eyebrow` + `h1` + `.sub`: the flow line, a large title, one sentence.
    // Aligned with the cards below it rather than indented past them.
    return Padding(
      padding: const EdgeInsets.fromLTRB(3, EcomsbdSpacing.xxs, 3, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (eyebrow != null) ...<Widget>[
            Text(
              eyebrow!.toUpperCase(),
              style: trackedFor(
                eyebrow!,
                EcomsbdType.eyebrowWide.copyWith(
                  color: EcomsbdColors.eyebrowInk,
                ),
              ),
            ),
            const SizedBox(height: 6),
          ],
          Text(title, style: EcomsbdType.screenTitle),
          if (description != null) ...<Widget>[
            const SizedBox(height: 6),
            Text(
              description!,
              style: EcomsbdType.body.copyWith(
                color: EcomsbdColors.muted,
                fontSize: 14,
                height: 1.4,
              ),
            ),
          ],
        ],
      ),
    );
  }
}
