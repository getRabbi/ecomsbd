import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';

/// Puts the offline strip above every screen — splash, sign-in, the shell and
/// anything pushed over it — while the app cannot reach the server.
///
/// The strip takes the status-bar inset and the screen below loses it, so the
/// content moves down by the strip's height rather than being covered. The
/// screen stays under the same widget either way: switching would remount the
/// navigator and throw away half-entered forms.
class NetworkStatusFrame extends ConsumerWidget {
  const NetworkStatusFrame({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final offline = ref.watch(isOfflineProvider);
    final media = MediaQuery.of(context);
    return Column(
      children: <Widget>[
        AnimatedSize(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
          alignment: Alignment.topCenter,
          child: offline
              ? OfflineStrip(topInset: media.padding.top)
              : const SizedBox(width: double.infinity),
        ),
        Expanded(
          child: MediaQuery(
            data: offline ? media.removePadding(removeTop: true) : media,
            child: child,
          ),
        ),
      ],
    );
  }
}

/// "No internet connection — Check your connection and try again", with a
/// Retry that asks the server at once instead of waiting for the next probe.
class OfflineStrip extends ConsumerStatefulWidget {
  const OfflineStrip({super.key, this.topInset = 0});

  /// The status-bar height the strip draws under.
  final double topInset;

  /// Warm, opaque, and dark enough text to read in sunlight (6:1 or better).
  static const Color background = Color(0xFFFFF1D6);
  static const Color inkSoft = Color(0xFF6E4700);

  @override
  ConsumerState<OfflineStrip> createState() => _OfflineStripState();
}

class _OfflineStripState extends ConsumerState<OfflineStrip> {
  bool _checking = false;

  Future<void> _retry() async {
    setState(() => _checking = true);
    try {
      await ref.read(networkMonitorProvider.notifier).checkNow();
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      container: true,
      child: Material(
        color: OfflineStrip.background,
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            EcomsbdSpacing.md,
            widget.topInset + EcomsbdSpacing.xs,
            EcomsbdSpacing.xs,
            EcomsbdSpacing.xs,
          ),
          child: Row(
            children: <Widget>[
              const Icon(
                Icons.cloud_off_rounded,
                size: 20,
                color: EcomsbdColors.amber,
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: <Widget>[
                    Text(
                      context.tr('net.offlineTitle'),
                      style: EcomsbdType.bodyStrong.copyWith(
                        color: EcomsbdColors.ink,
                      ),
                    ),
                    Text(
                      context.tr('net.offlineBody'),
                      style: EcomsbdType.caption.copyWith(
                        color: OfflineStrip.inkSoft,
                      ),
                    ),
                  ],
                ),
              ),
              TextButton(
                onPressed: _checking ? null : _retry,
                style: TextButton.styleFrom(
                  foregroundColor: EcomsbdColors.ink,
                  minimumSize: const Size(0, EcomsbdTouch.minTarget),
                  textStyle: EcomsbdType.label,
                ),
                child: Text(
                  context.tr(_checking ? 'net.checking' : 'common.retry'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// For a screen that loads outside Riverpod: [onReconnect] runs once each
/// time the app comes back online, so a screen left on "No internet
/// connection" fills in by itself.
mixin ReloadOnReconnect<T extends ConsumerStatefulWidget> on ConsumerState<T> {
  /// Reload whatever failed. Called only on the offline-to-online change.
  void onReconnect();

  @override
  void initState() {
    super.initState();
    ref.listenManual<bool>(isOfflineProvider, (previous, offline) {
      if (previous == true && !offline && mounted) onReconnect();
    });
  }
}
