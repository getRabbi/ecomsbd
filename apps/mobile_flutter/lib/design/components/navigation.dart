import 'package:flutter/material.dart';

import '../../l10n/app_strings.dart';
import '../glass.dart';
import '../tokens.dart';

/// The four main destinations (master spec section 3).
///
/// Fixed at four, in this order. The bottom bar is the app's spine and
/// reordering it would relearn every seller's muscle memory.
///
/// Menu is not one of them. It opens from the hamburger in the top bar, which
/// every main tab carries; a Menu tab as well was a second door to the same
/// room, and it cost the four real destinations a fifth of the bar.
enum MainDestination { home, orders, money, insights }

extension MainDestinationLabel on MainDestination {
  /// The tab's name in the selected language.
  ///
  /// The enum name is the translation key, so adding a destination cannot
  /// leave its label behind: `nav.orders` is looked up from `name`.
  String labelIn(BuildContext context) => context.tr('nav.$name');

  IconData get icon => switch (this) {
    MainDestination.home => Icons.home_rounded,
    MainDestination.orders => Icons.receipt_long_rounded,
    MainDestination.money => Icons.payments_rounded,
    MainDestination.insights => Icons.insights_rounded,
  };
}

/// `.bottom-nav` — the floating glass bar with a raised circular active item.
///
/// The active item lifts out of the bar and sits inside its own white circle
/// with an orange dot; that lift is the signature of the locked design and is
/// reproduced here rather than approximated with a Material indicator.
class BottomGlassNavigation extends StatelessWidget {
  const BottomGlassNavigation({
    required this.current,
    required this.onSelected,
    super.key,
  });

  final MainDestination current;
  final ValueChanged<MainDestination> onSelected;

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.viewPaddingOf(context).bottom;

    // The bar is fixed while the page scrolls beneath it, and it still carries
    // a live blur. Its own layer keeps every scroll frame from repainting the
    // raised active item and its shadows along with the content.
    return RepaintBoundary(
      child: Padding(
        padding: EdgeInsets.fromLTRB(11, 0, 11, 8 + bottomInset),
        child: SizedBox(
          height: EcomsbdTouch.bottomNavHeight,
          child: Stack(
            fit: StackFit.expand,
            clipBehavior: Clip.none,
            children: <Widget>[
              // Clip the backdrop to the pill, while allowing the active item
              // to rise above it without expanding the blur to the whole page.
              const Positioned.fill(
                child: GlassSurface(
                  borderRadius: EcomsbdRadii.round,
                  fill: Color(0xB0FFFFFF),
                  borderColor: Color(0xFAFFFFFF),
                  shadows: EcomsbdShadows.bottomNav,
                  blurSigma: 24,
                  child: SizedBox.expand(),
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 7),
                child: Row(
                  children: <Widget>[
                    for (final destination in MainDestination.values)
                      Expanded(
                        child: _NavItem(
                          destination: destination,
                          isActive: destination == current,
                          onTap: () => onSelected(destination),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _NavItem extends StatelessWidget {
  const _NavItem({
    required this.destination,
    required this.isActive,
    required this.onTap,
  });

  final MainDestination destination;
  final bool isActive;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      selected: isActive,
      button: true,
      label: destination.labelIn(context),
      child: InkWell(
        onTap: onTap,
        customBorder: const CircleBorder(),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOutCubic,
          transform: Matrix4.translationValues(0, isActive ? -13 : 0, 0),
          transformAlignment: Alignment.center,
          child: Stack(
            alignment: Alignment.center,
            clipBehavior: Clip.none,
            children: <Widget>[
              if (isActive)
                Container(
                  width: 61,
                  height: 61,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: const Color(0xEDFFFFFF),
                    border: Border.all(color: const Color(0xFCFFFFFF)),
                    boxShadow: EcomsbdShadows.pill,
                  ),
                ),
              Column(
                mainAxisSize: MainAxisSize.min,
                children: <Widget>[
                  Icon(
                    destination.icon,
                    size: 21,
                    color: isActive
                        ? const Color(0xFF111820)
                        : EcomsbdColors.muted,
                  ),
                  const SizedBox(height: 2),
                  Text(
                    destination.labelIn(context),
                    style: EcomsbdType.eyebrow.copyWith(
                      letterSpacing: 0,
                      color: isActive
                          ? const Color(0xFF111820)
                          : EcomsbdColors.muted,
                    ),
                  ),
                ],
              ),
              if (isActive)
                Positioned(
                  top: -2,
                  right: 12,
                  child: Container(
                    width: 7,
                    height: 7,
                    decoration: const BoxDecoration(
                      shape: BoxShape.circle,
                      color: EcomsbdColors.orange,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// `.modal` — the frosted bottom sheet used for new order, payout and courier
/// forms.
class GlassBottomSheet extends StatelessWidget {
  const GlassBottomSheet({
    required this.title,
    required this.child,
    super.key,
    this.description,
    this.actions = const <Widget>[],
  });

  final String title;
  final String? description;
  final Widget child;
  final List<Widget> actions;

  /// Present this sheet. Scrollable and inset above the keyboard so a long
  /// form on a small screen stays usable.
  static Future<T?> show<T>({
    required BuildContext context,
    required String title,
    required Widget child,
    String? description,
    List<Widget> actions = const <Widget>[],
  }) {
    return showModalBottomSheet<T>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      barrierColor: const Color(0x470B121C),
      builder: (context) => GlassBottomSheet(
        title: title,
        description: description,
        actions: actions,
        child: child,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final viewInsets = MediaQuery.viewInsetsOf(context).bottom;

    return Padding(
      padding: EdgeInsets.only(
        left: EcomsbdSpacing.md,
        right: EcomsbdSpacing.md,
        bottom: EcomsbdSpacing.md + viewInsets,
      ),
      child: ConstrainedBox(
        constraints: BoxConstraints(
          maxHeight: MediaQuery.sizeOf(context).height * 0.89,
        ),
        child: GlassSurface(
          fill: const Color(0xF2FFFFFF),
          borderRadius: const BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.xl),
            bottom: Radius.circular(EcomsbdRadii.lg),
          ),
          shadows: EcomsbdShadows.strong,
          blurSigma: 24,
          padding: const EdgeInsets.all(EcomsbdSpacing.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(child: Text(title, style: EcomsbdType.heroTitle)),
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close_rounded),
                    tooltip: context.tr('common.close'),
                    constraints: const BoxConstraints(
                      minWidth: EcomsbdTouch.minTarget,
                      minHeight: EcomsbdTouch.minTarget,
                    ),
                  ),
                ],
              ),
              if (description != null) ...<Widget>[
                Text(
                  description!,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
                const SizedBox(height: EcomsbdSpacing.md),
              ],
              Flexible(child: SingleChildScrollView(child: child)),
              if (actions.isNotEmpty) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.md),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: <Widget>[
                    for (final action in actions) ...<Widget>[
                      action,
                      const SizedBox(width: EcomsbdSpacing.xs),
                    ],
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
