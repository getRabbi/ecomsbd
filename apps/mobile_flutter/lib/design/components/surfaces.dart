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
              if (topBar != null)
                Positioned(
                  left: 0,
                  right: 0,
                  top: 0,
                  child: SafeArea(bottom: false, child: topBar!),
                ),
              if (bottomBar != null)
                Positioned(left: 0, right: 0, bottom: 0, child: bottomBar!),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.glass-card` — the standard frosted card.
class GlassCard extends StatelessWidget {
  const GlassCard({
    required this.child,
    super.key,
    this.padding = const EdgeInsets.all(EcomsbdSpacing.md),
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
      margin: margin,
      borderRadius: borderRadius,
      child: _Tappable(
        onTap: onTap,
        borderRadius: borderRadius,
        child: Padding(padding: padding, child: child),
      ),
    );
  }
}

/// `.glass-card.glass-strong` — a more opaque card with a deeper shadow, used
/// where content must stay legible over a busy hero.
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
      fill: EcomsbdColors.glassStrong,
      shadows: EcomsbdShadows.strong,
      margin: margin,
      borderRadius: borderRadius,
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
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        3,
        EcomsbdSpacing.md,
        3,
        EcomsbdSpacing.sm,
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(title, style: EcomsbdType.sectionTitle),
                if (subtitle != null) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    subtitle!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
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
                minimumSize: const Size(0, EcomsbdTouch.minTarget),
                padding: const EdgeInsets.symmetric(horizontal: 10),
                foregroundColor: EcomsbdColors.orange,
                textStyle: EcomsbdType.chip,
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
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.lg,
        0,
        EcomsbdSpacing.lg,
        EcomsbdSpacing.md,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          if (eyebrow != null)
            Text(
              eyebrow!.toUpperCase(),
              style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
            ),
          const SizedBox(height: 4),
          Text(title, style: EcomsbdType.pageTitle),
          if (description != null) ...<Widget>[
            const SizedBox(height: 5),
            Text(
              description!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }
}
