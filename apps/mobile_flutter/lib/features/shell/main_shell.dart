import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/analytics/analytics_providers.dart';
import '../../design/components/navigation.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/theme.dart';
import '../../design/tokens.dart';
import '../home/home_screen.dart';
import '../insights/insights_screen.dart';
import '../menu/menu_overlay.dart';
import '../money/money_screen.dart';
import '../notifications/notification_centre_screen.dart';
import '../orders/orders_screen.dart';
import '../search/search_screen.dart';
import '../shared/responsive.dart';

/// The signed-in shell: four tabs plus the menu overlay.
///
/// Tab state is kept in an [IndexedStack] so scroll position and any in-progress
/// form survive switching tabs — a seller mid-way through entering an order must
/// not lose it by glancing at Money.
class MainShell extends ConsumerStatefulWidget {
  const MainShell({super.key, this.initialTab = MainDestination.home});

  final MainDestination initialTab;

  @override
  ConsumerState<MainShell> createState() => MainShellState();
}

class MainShellState extends ConsumerState<MainShell> {
  late MainDestination _current = widget.initialTab;

  static const List<MainDestination> _tabs = <MainDestination>[
    MainDestination.home,
    MainDestination.orders,
    MainDestination.money,
    MainDestination.insights,
  ];

  int get _index => _tabs.indexOf(_current).clamp(0, _tabs.length - 1);

  void _select(MainDestination destination) {
    if (destination == MainDestination.menu) {
      _openMenu();
      return;
    }
    setState(() => _current = destination);
  }

  Future<void> _openMenu() async {
    await MenuOverlay.show(context);
  }

  void _openSearch() {
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const GlobalSearchScreen()));
  }

  void _navigateByName(String name) {
    switch (name) {
      case 'orders':
        _select(MainDestination.orders);
      case 'money':
        _select(MainDestination.money);
      case 'insights':
        _select(MainDestination.insights);
      case 'home':
        _select(MainDestination.home);
      default:
        _openMenu();
    }
  }

  @override
  Widget build(BuildContext context) {
    final isHome = _current == MainDestination.home;

    return EcomsbdScaffold(
      // Home's navy hero runs under the status bar, so its icons flip to light.
      overlayStyle: isHome ? ecomsbdDarkOverlay : ecomsbdLightOverlay,
      extendBehindTopBar: isHome,
      topBar: GlassTopBar(
        leading: isHome
            ? BrandPill(onTap: _openMenu)
            : BrandPill(
                label: _current.label,
                mark: _current.label.substring(0, 1).toLowerCase(),
                onTap: _openMenu,
                markColors: const <Color>[
                  EcomsbdColors.navyLight,
                  EcomsbdColors.navyMid,
                ],
              ),
        actions: <Widget>[
          GlassIconButton(
            icon: Icons.add_rounded,
            tooltip: 'New order',
            onPressed: () => _select(MainDestination.orders),
          ),
          GlassIconButton(
            icon: Icons.search_rounded,
            tooltip: 'Search ecomsbd',
            onPressed: _openSearch,
          ),
          _NotificationButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const NotificationCentreScreen(),
              ),
            ),
          ),
          GlassIconButton(
            icon: Icons.menu_rounded,
            tooltip: 'Menu',
            onPressed: _openMenu,
          ),
        ],
      ),
      bottomBar: BottomGlassNavigation(current: _current, onSelected: _select),
      child: ContentWidthLimit(
        child: IndexedStack(
          index: _index,
          children: <Widget>[
            HomeScreen(onOpenMenu: _openMenu, onNavigate: _navigateByName),
            OrdersScreen(onNavigate: _navigateByName),
            MoneyScreen(onNavigate: _navigateByName),
            const InsightsScreen(),
          ],
        ),
      ),
    );
  }
}

/// The bell, with the unread count on it.
///
/// The count is a plain number rather than a red dot: master spec section 94
/// is about the seller being able to find out how much needs them, and "3"
/// answers that where a dot only says "something".
class _NotificationButton extends ConsumerWidget {
  const _NotificationButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadNotificationCountProvider).valueOrNull ?? 0;

    return Stack(
      clipBehavior: Clip.none,
      children: <Widget>[
        GlassIconButton(
          icon: unread > 0
              ? Icons.notifications_active_rounded
              : Icons.notifications_none_rounded,
          tooltip: unread > 0 ? '$unread unread' : 'Notifications',
          onPressed: onPressed,
        ),
        if (unread > 0)
          Positioned(
            top: -2,
            right: -2,
            child: IgnorePointer(
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                constraints: const BoxConstraints(minWidth: 18),
                decoration: BoxDecoration(
                  color: EcomsbdColors.red,
                  borderRadius: BorderRadius.circular(9),
                ),
                child: Text(
                  unread > 9 ? '9+' : '$unread',
                  textAlign: TextAlign.center,
                  style: EcomsbdType.chip.copyWith(
                    color: Colors.white,
                    fontSize: 10,
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
