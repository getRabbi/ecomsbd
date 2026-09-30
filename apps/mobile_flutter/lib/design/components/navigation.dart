import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../l10n/app_strings.dart';
import '../glass.dart';
import '../tokens.dart';

/// The five main destinations, in this order.
///
/// Home, Orders, Inbox, Money and More. Everything else — Insights, products,
/// customers, courier tools, settings — opens from More or from the screen
/// that needs it, so the bar never grows past five. More replaces the old
/// hamburger menu rather than duplicating it.
enum MainDestination { home, orders, inbox, money, more }

extension MainDestinationLabel on MainDestination {
  /// The tab's name in the selected language.
  ///
  /// The enum name is the translation key, so adding a destination cannot
  /// leave its label behind: `nav.orders` is looked up from `name`.
  String labelIn(BuildContext context) => context.tr('nav.$name');

  IconData get icon => switch (this) {
    MainDestination.home => Icons.home_rounded,
    MainDestination.orders => Icons.receipt_long_rounded,
    MainDestination.inbox => Icons.forum_rounded,
    MainDestination.money => Icons.payments_rounded,
    MainDestination.more => Icons.grid_view_rounded,
  };
}

/// `.bottom-nav` — the floating glass bar of five equal slots.
///
/// The selected slot is a white rounded tile inside the bar, with the icon and
/// label in ink; the others are muted. Nothing rises out of the bar and there
/// is no second indicator, matching the final prototype's compact hierarchy.
class BottomGlassNavigation extends StatelessWidget {
  const BottomGlassNavigation({
    required this.current,
    required this.onSelected,
    super.key,
  });

  final MainDestination current;
  final ValueChanged<MainDestination> onSelected;

  /// Marks the selected slot's tile, so tests can find it.
  static const Key activeTileKey = ValueKey<String>('bottom-nav-active');

  @override
  Widget build(BuildContext context) {
    final bottomInset = MediaQuery.viewPaddingOf(context).bottom;

    // The bar is fixed while the page scrolls beneath it, and it still carries
    // a live blur. Its own layer keeps every scroll frame from repainting it
    // along with the content.
    return RepaintBoundary(
      child: Padding(
        padding: EdgeInsets.fromLTRB(14, 0, 14, 10 + bottomInset),
        child: GlassSurface(
          borderRadius: BorderRadius.circular(30),
          fill: const Color(0xE6FAFCFD),
          borderColor: const Color(0xF2FFFFFF),
          shadows: const <BoxShadow>[
            BoxShadow(
              color: Color(0x29121F30),
              blurRadius: 45,
              offset: Offset(0, 15),
            ),
          ],
          blurSigma: 24,
          child: SizedBox(
            height: EcomsbdTouch.bottomNavHeight,
            child: Padding(
              padding: const EdgeInsets.all(7),
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
    final color = isActive ? EcomsbdColors.ink : EcomsbdColors.navInactive;
    const radius = BorderRadius.all(Radius.circular(24));
    return Semantics(
      selected: isActive,
      button: true,
      label: destination.labelIn(context),
      child: InkWell(
        onTap: onTap,
        borderRadius: radius,
        child: AnimatedContainer(
          key: isActive ? BottomGlassNavigation.activeTileKey : null,
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOutCubic,
          decoration: BoxDecoration(
            color: isActive ? Colors.white : Colors.transparent,
            borderRadius: radius,
            boxShadow: isActive
                ? const <BoxShadow>[
                    BoxShadow(
                      color: Color(0x14141F30),
                      blurRadius: 18,
                      offset: Offset(0, 5),
                    ),
                  ]
                : null,
          ),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: <Widget>[
              Icon(destination.icon, size: 21, color: color),
              const SizedBox(height: 3),
              Text(
                destination.labelIn(context),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: EcomsbdType.chip.copyWith(
                  fontSize: 11,
                  fontWeight: isActive ? FontWeight.w800 : FontWeight.w700,
                  color: color,
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
    // The keyboard when it is open, otherwise the home indicator or gesture
    // bar: the floating sheet sits above whichever reaches higher.
    final bottomClearance = math.max(
      MediaQuery.viewInsetsOf(context).bottom,
      MediaQuery.viewPaddingOf(context).bottom,
    );

    return Padding(
      padding: EdgeInsets.only(
        left: EcomsbdSpacing.md,
        right: EcomsbdSpacing.md,
        bottom: EcomsbdSpacing.md + bottomClearance,
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
