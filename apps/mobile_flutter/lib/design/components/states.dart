import 'package:flutter/material.dart';

import '../../l10n/app_strings.dart';
import '../glass.dart';
import '../tokens.dart';
import 'badges.dart';

/// Shown while the device has no connection.
///
/// Master spec sections 37 and 127: offline state is explicit, and the banner
/// is careful to say what still works. Sellers keep taking orders on bad
/// connections; the app's job is to be honest about what is queued, not to
/// look broken.
class OfflineBanner extends StatelessWidget {
  const OfflineBanner({super.key, this.pendingCount = 0, this.onRetry});

  /// Locally-created records not yet accepted by the server.
  final int pendingCount;

  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final detail = pendingCount > 0
        ? context.trPlural('offline.detail', pendingCount)
        : context.tr('offline.detailPlain');

    return _Banner(
      tone: Tone.warning,
      icon: Icons.cloud_off_rounded,
      title: context.tr('common.offline'),
      detail: detail,
      actionLabel: onRetry == null ? null : context.tr('common.retry'),
      onAction: onRetry,
    );
  }
}

/// Shown when a courier provider is degraded or disabled.
///
/// Master spec section 75: only the affected network action is blocked.
/// Offline and manual work continue, and historical data stays available, so
/// the banner names the limit rather than blocking the screen.
class ProviderHealthBanner extends StatelessWidget {
  const ProviderHealthBanner({
    required this.provider,
    required this.detail,
    super.key,
    this.tone = Tone.warning,
    this.actionLabel,
    this.onAction,
  });

  final String provider;
  final String detail;
  final Tone tone;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return _Banner(
      tone: tone,
      icon: Icons.local_shipping_outlined,
      title: provider,
      detail: detail,
      actionLabel: actionLabel,
      onAction: onAction,
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({
    required this.tone,
    required this.icon,
    required this.title,
    required this.detail,
    this.actionLabel,
    this.onAction,
  });

  final Tone tone;
  final IconData icon;
  final String title;
  final String detail;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: tone.surface,
      borderColor: tone.ink.withValues(alpha: 0.18),
      borderRadius: EcomsbdRadii.cardMedium,
      shadows: const <BoxShadow>[],
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(icon, size: 19, color: tone.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Text(
                  title,
                  style: EcomsbdType.bodyStrong.copyWith(color: tone.ink),
                ),
                const SizedBox(height: 2),
                Text(
                  detail,
                  style: EcomsbdType.caption.copyWith(color: tone.ink),
                ),
              ],
            ),
          ),
          if (actionLabel != null) ...<Widget>[
            const SizedBox(width: EcomsbdSpacing.xs),
            TextButton(
              onPressed: onAction,
              style: TextButton.styleFrom(
                foregroundColor: tone.ink,
                minimumSize: const Size(0, EcomsbdTouch.minTarget),
                textStyle: EcomsbdType.chip,
              ),
              child: Text(actionLabel!),
            ),
          ],
        ],
      ),
    );
  }
}

/// An empty state that explains the next action.
///
/// Master spec section 52: "empty states explain next action". A blank screen
/// with an illustration tells a seller nothing about what to do.
class EmptyState extends StatelessWidget {
  const EmptyState({
    required this.icon,
    required this.title,
    required this.message,
    super.key,
    this.actionLabel,
    this.onAction,
  });

  final IconData icon;
  final String title;
  final String message;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(
        horizontal: EcomsbdSpacing.xl,
        vertical: EcomsbdSpacing.xxl,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          Container(
            width: 56,
            height: 56,
            alignment: Alignment.center,
            decoration: BoxDecoration(
              color: EcomsbdColors.orangeSoft,
              borderRadius: BorderRadius.circular(18),
            ),
            child: Icon(icon, size: 26, color: EcomsbdColors.orange),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          Text(
            title,
            style: EcomsbdType.sectionTitle,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            message,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            textAlign: TextAlign.center,
          ),
          if (actionLabel != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.lg),
            FilledButton(
              onPressed: onAction,
              style: FilledButton.styleFrom(
                backgroundColor: EcomsbdColors.orange,
                minimumSize: const Size(0, EcomsbdTouch.minTarget),
                shape: const StadiumBorder(),
                textStyle: EcomsbdType.label,
              ),
              child: Text(actionLabel!),
            ),
          ],
        ],
      ),
    );
  }
}

/// A shimmering placeholder block.
///
/// Master spec section 52: "skeletons, not spinner forever". A skeleton keeps
/// the layout stable so content does not jump when it arrives, which matters
/// on the slow connections this app targets.
class SkeletonLoader extends StatefulWidget {
  const SkeletonLoader({
    super.key,
    this.width,
    this.height = 16,
    this.borderRadius,
  });

  /// A stack of lines approximating a card.
  factory SkeletonLoader.card({double height = 120}) =>
      SkeletonLoader(height: height, borderRadius: EcomsbdRadii.cardLarge);

  final double? width;
  final double height;
  final BorderRadius? borderRadius;

  @override
  State<SkeletonLoader> createState() => _SkeletonLoaderState();
}

class _SkeletonLoaderState extends State<SkeletonLoader>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    // The shimmer runs at 60fps for as long as the screen is loading. Its own
    // layer keeps that repaint from marking the whole page dirty each frame.
    return RepaintBoundary(
      child: AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          return Container(
            width: widget.width,
            height: widget.height,
            decoration: BoxDecoration(
              color: Color.lerp(
                const Color(0xFFE7ECF0),
                const Color(0xFFF3F6F8),
                _controller.value,
              ),
              borderRadius: widget.borderRadius ?? EcomsbdRadii.cardSmall,
            ),
          );
        },
      ),
    );
  }
}

/// A screen-level skeleton for the dashboard while its first load runs.
class DashboardSkeleton extends StatelessWidget {
  const DashboardSkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: EcomsbdSpacing.page),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SkeletonLoader.card(height: 150),
          const SizedBox(height: EcomsbdSpacing.md),
          Row(
            children: <Widget>[
              Expanded(child: SkeletonLoader.card(height: 94)),
              const SizedBox(width: EcomsbdSpacing.xs),
              Expanded(child: SkeletonLoader.card(height: 94)),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          SkeletonLoader.card(height: 200),
        ],
      ),
    );
  }
}

/// A spinner centred in the usable content area.
///
/// A bare `Center(child: CircularProgressIndicator())` inside a scroll view
/// collapses to the top of the page, which put the app's first-load spinner
/// under the floating top bar. This reserves a slice of the viewport and
/// centres in it, so an initial load reads as "this area is loading" wherever
/// it is used.
class ContentLoader extends StatelessWidget {
  const ContentLoader({super.key, this.minHeight, this.message});

  /// Defaults to a little under half the viewport, so the spinner lands in the
  /// optical centre of the content rather than against the header.
  final double? minHeight;

  final String? message;

  @override
  Widget build(BuildContext context) {
    final fallback = MediaQuery.sizeOf(context).height * 0.42;
    return ConstrainedBox(
      constraints: BoxConstraints(minHeight: minHeight ?? fallback),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const SizedBox(
              width: 26,
              height: 26,
              child: CircularProgressIndicator(strokeWidth: 2.4),
            ),
            if (message != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.md),
              Text(
                message!,
                textAlign: TextAlign.center,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
