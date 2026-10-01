import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../data/analytics/analytics_providers.dart';
import '../../data/chat_orders/chat_orders.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/notifications/push_registration.dart';
import '../../design/components/navigation.dart';
import '../../design/components/pills.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../home/home_screen.dart';
import '../inbox/inbox_screen.dart';
import '../menu/more_screen.dart';
import '../money/money_screen.dart';
import '../notifications/notification_centre_screen.dart';
import '../orders/orders_screen.dart';
import '../search/search_screen.dart';
import '../shared/responsive.dart';

/// The signed-in shell: five tabs — Home, Orders, Inbox, Money, More.
///
/// More is the one door to every other tool; the top bar carries only new
/// order, search and notifications, and the title pill is a label.
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

  /// Tabs the seller has actually opened.
  ///
  /// An [IndexedStack] builds every child eagerly, so every tab used to
  /// load their data during launch. Unopened tabs are a placeholder until
  /// first use; opened ones stay mounted, which is what keeps scroll position
  /// and half-entered forms alive across a tab switch.
  late final Set<MainDestination> _opened = <MainDestination>{
    widget.initialTab,
  };

  static const List<MainDestination> _tabs = MainDestination.values;

  StreamSubscription<Map<String, dynamic>>? _pushTaps;

  @override
  void initState() {
    super.initState();
    // A tapped push lands on what it is about: a chat order opens the Inbox.
    _pushTaps = pushTaps().listen(_openFromPush);
    unawaited(
      initialPushTap().then((data) {
        if (data != null && mounted) _openFromPush(data);
      }),
    );
  }

  @override
  void dispose() {
    unawaited(_pushTaps?.cancel());
    super.dispose();
  }

  void _openFromPush(Map<String, dynamic> data) {
    if (data['route'] == 'inbox' || data['kind'] == 'CHAT_ORDER_READY') {
      ref.invalidate(chatDraftsProvider);
      ref.invalidate(chatOrderSummaryProvider);
      _select(MainDestination.inbox);
    }
  }

  int get _index => _tabs.indexOf(_current).clamp(0, _tabs.length - 1);

  void _select(MainDestination destination) {
    setState(() {
      _current = destination;
      _opened.add(destination);
    });
  }

  void _openSearch() {
    Navigator.of(
      context,
    ).push(MaterialPageRoute<void>(builder: (_) => const GlobalSearchScreen()));
  }

  /// Route names used by Home, Money and More.
  ///
  /// `orders:<group>` opens Orders on one filter group, e.g.
  /// `orders:confirmed` for orders waiting for a courier booking.
  void _navigateByName(String name) {
    final parts = name.split(':');
    switch (parts.first) {
      case 'orders':
        if (parts.length > 1) {
          final group = OrderFilterGroup.values.where(
            (value) => value.name == parts[1],
          );
          if (group.isNotEmpty) {
            ref.read(orderListProvider.notifier).setGroup(group.first);
          }
        }
        _select(MainDestination.orders);
      case 'inbox':
        _select(MainDestination.inbox);
      case 'money':
        _select(MainDestination.money);
      case 'insights':
        unawaited(
          Navigator.of(
            context,
          ).push(MaterialPageRoute<void>(builder: (_) => const InsightsPage())),
        );
      case 'home':
        _select(MainDestination.home);
      default:
        _select(MainDestination.more);
    }
  }

  /// The body for one tab, or a placeholder if it has never been opened.
  Widget _tabBody(MainDestination tab) {
    if (!_opened.contains(tab)) {
      return const SizedBox.shrink();
    }
    return switch (tab) {
      MainDestination.home => HomeScreen(onNavigate: _navigateByName),
      MainDestination.orders => OrdersScreen(onNavigate: _navigateByName),
      MainDestination.inbox => InboxScreen(onNavigate: _navigateByName),
      MainDestination.money => MoneyScreen(onNavigate: _navigateByName),
      MainDestination.more => MoreScreen(onNavigate: _navigateByName),
    };
  }

  @override
  Widget build(BuildContext context) {
    // Every tab, Home included, starts below the bar on the light page: the
    // Home summary is a card in the page, not a full-bleed band under it.
    return EcomsbdScaffold(
      topBar: GlassTopBar(
        // The current section's name, as a label. Every tab reads the same
        // way; every other tool lives under More.
        leading: BrandPill(label: _current.labelIn(context)),
        actions: <Widget>[
          GlassIconButton(
            icon: Icons.add_rounded,
            tooltip: context.tr('nav.newOrderTooltip'),
            onPressed: () => _select(MainDestination.orders),
          ),
          GlassIconButton(
            icon: Icons.search_rounded,
            tooltip: context.tr('nav.searchTooltip'),
            onPressed: _openSearch,
          ),
          _NotificationButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute<void>(
                builder: (_) => const NotificationCentreScreen(),
              ),
            ),
          ),
        ],
      ),
      bottomBar: BottomGlassNavigation(current: _current, onSelected: _select),
      child: ContentWidthLimit(
        child: IndexedStack(
          index: _index,
          children: <Widget>[for (final tab in _tabs) _tabBody(tab)],
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
///
/// The badge sits *inside* the button's 48dp square. The action pill clips to
/// its rounded outline, so a badge hung off the corner of the button (as it
/// once was) had its top sliced off by the pill edge. Keeping it within the
/// square keeps it within the pill, on every tab and at every width, without
/// moving the header.
class _NotificationButton extends ConsumerWidget {
  const _NotificationButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final unread = ref.watch(unreadNotificationCountProvider).valueOrNull ?? 0;

    return SizedBox.square(
      dimension: EcomsbdTouch.minTarget,
      child: Stack(
        clipBehavior: Clip.none,
        children: <Widget>[
          Positioned.fill(
            child: GlassIconButton(
              icon: unread > 0
                  ? Icons.notifications_active_rounded
                  : Icons.notifications_none_rounded,
              tooltip: unread > 0
                  ? context.tr('nav.unreadTooltip', <String, Object?>{
                      'count': unread,
                    })
                  : context.tr('nav.notificationsTooltip'),
              onPressed: onPressed,
            ),
          ),
          if (unread > 0)
            PositionedDirectional(
              top: NotificationCountBadge.inset,
              end: NotificationCountBadge.inset,
              // The tooltip already carries the count for a screen reader.
              child: IgnorePointer(
                child: ExcludeSemantics(
                  child: NotificationCountBadge(count: unread),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// The red count on the bell: `1`–`9`, `10`–`99`, then `99+`.
///
/// A fixed-height capsule that grows sideways only, so a three-character count
/// never pushes it past the top of the bar. Its text ignores the system font
/// scale: at 200% a 10sp digit would outgrow any badge that still fits the bar,
/// and the count is read out in full from the button's tooltip regardless.
class NotificationCountBadge extends StatelessWidget {
  const NotificationCountBadge({required this.count, super.key});

  final int count;

  /// Distance from the top and trailing edges of the 48dp button square.
  static const double inset = 5;

  static const double height = 16;

  /// The text shown for [count].
  static String labelFor(int count) => count > 99 ? '99+' : '$count';

  @override
  Widget build(BuildContext context) {
    return Container(
      height: height,
      constraints: const BoxConstraints(minWidth: height),
      padding: const EdgeInsets.symmetric(horizontal: 4),
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: EcomsbdColors.red,
        borderRadius: BorderRadius.circular(height / 2),
        // A hairline in the bar's own colour, so the badge reads as sitting on
        // the bell rather than merging into it.
        border: Border.all(color: Colors.white, width: 1.2),
      ),
      child: Text(
        labelFor(count),
        maxLines: 1,
        softWrap: false,
        textAlign: TextAlign.center,
        textScaler: TextScaler.noScaling,
        style: EcomsbdType.chip.copyWith(
          color: Colors.white,
          fontSize: 10,
          fontWeight: FontWeight.w800,
          height: 1,
          letterSpacing: 0,
          leadingDistribution: TextLeadingDistribution.even,
        ),
      ),
    );
  }
}
