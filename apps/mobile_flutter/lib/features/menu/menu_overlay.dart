import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../design/glass.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/responsive.dart';

/// One tile in the menu grid.
class _MenuItem {
  const _MenuItem({
    required this.icon,
    required this.title,
    required this.subtitle,
    this.phase,
  });

  final IconData icon;
  final String title;
  final String subtitle;

  /// The phase that ships this destination. Present means "not built yet",
  /// and the tile says so rather than opening an empty screen.
  final String? phase;

  bool get isAvailable => phase == null;
}

/// The full-screen menu overlay (`.menu-screen`).
///
/// Master spec section 3 lists everything under "More". Destinations that have
/// not shipped are shown with the phase that will bring them, because a seller
/// evaluating the app should be able to see its shape — and because a tile that
/// opens a blank screen is worse than one that explains itself.
class MenuOverlay extends ConsumerWidget {
  const MenuOverlay({super.key});

  static Future<void> show(BuildContext context) {
    return Navigator.of(context).push(
      PageRouteBuilder<void>(
        opaque: false,
        barrierColor: Colors.transparent,
        transitionDuration: const Duration(milliseconds: 220),
        pageBuilder: (_, __, ___) => const MenuOverlay(),
        transitionsBuilder: (_, animation, __, child) =>
            FadeTransition(opacity: animation, child: child),
      ),
    );
  }

  static const List<_MenuItem> _operations = <_MenuItem>[
    _MenuItem(
      icon: Icons.inventory_2_outlined,
      title: 'Products & stock',
      subtitle: 'Cost · margin · inventory',
      phase: 'Phase B',
    ),
    _MenuItem(
      icon: Icons.people_outline,
      title: 'Customers',
      subtitle: 'Private CRM · repeat buyers',
      phase: 'Phase B',
    ),
    _MenuItem(
      icon: Icons.shield_outlined,
      title: 'Risk check',
      subtitle: 'Delivery history signal',
      phase: 'Phase C',
    ),
    _MenuItem(
      icon: Icons.local_shipping_outlined,
      title: 'Courier accounts',
      subtitle: 'BYOC · provider health',
      phase: 'Phase C',
    ),
    _MenuItem(
      icon: Icons.assignment_return_outlined,
      title: 'Return center',
      subtitle: 'Loss · reasons · stock',
      phase: 'Phase E',
    ),
    _MenuItem(
      icon: Icons.payments_outlined,
      title: 'Expenses & ads',
      subtitle: 'Profit inputs · allocation',
      phase: 'Phase E',
    ),
  ];

  static const List<_MenuItem> _money = <_MenuItem>[
    _MenuItem(
      icon: Icons.rule_folder_outlined,
      title: 'Reconciliation',
      subtitle: 'Match · mismatch · dispute',
      phase: 'Phase D',
    ),
    _MenuItem(
      icon: Icons.receipt_long_outlined,
      title: 'Payouts',
      subtitle: 'API · CSV · manual',
      phase: 'Phase D',
    ),
    _MenuItem(
      icon: Icons.file_download_outlined,
      title: 'Imports / exports',
      subtitle: 'Sheets · statements · own data',
      phase: 'Phase B',
    ),
    _MenuItem(
      icon: Icons.notifications_none_rounded,
      title: 'Notifications',
      subtitle: 'Actionable alerts',
      phase: 'Phase E',
    ),
  ];

  static const List<_MenuItem> _account = <_MenuItem>[
    _MenuItem(
      icon: Icons.groups_outlined,
      title: 'Team',
      subtitle: 'Roles · activity',
      phase: 'V1.1',
    ),
    _MenuItem(
      icon: Icons.star_outline_rounded,
      title: 'Subscription',
      subtitle: 'Plan · entitlements',
      phase: 'Phase F',
    ),
    _MenuItem(
      icon: Icons.settings_outlined,
      title: 'Settings',
      subtitle: 'Shop · SMS · privacy',
      phase: 'Phase B',
    ),
    _MenuItem(
      icon: Icons.help_outline_rounded,
      title: 'Support',
      subtitle: 'Case + WhatsApp',
      phase: 'Phase F',
    ),
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final profile = ref.watch(authControllerProvider).profile;
    final shopName = profile?.activeTenant?.name ?? 'Your shop';
    final role = profile?.role ?? 'OWNER';

    return GlassSurface(
      fill: const Color(0xCFEEF2F6),
      borderRadius: BorderRadius.zero,
      borderColor: null,
      shadows: const <BoxShadow>[],
      blurSigma: 26,
      child: SafeArea(
        child: ContentWidthLimit(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(
              EcomsbdSpacing.md,
              EcomsbdSpacing.lg,
              EcomsbdSpacing.md,
              EcomsbdSpacing.xxl,
            ),
            children: <Widget>[
              Row(
                children: <Widget>[
                  // The locked product name. This is the menu's title in the
                  // prototype, and it is the app's identity.
                  const Expanded(
                    child: Text('ecomsbd', style: EcomsbdType.pageTitle),
                  ),
                  IconButton(
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close_rounded),
                    tooltip: 'Close menu',
                    iconSize: 22,
                    constraints: const BoxConstraints(
                      minWidth: EcomsbdTouch.minTarget,
                      minHeight: EcomsbdTouch.minTarget,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              StrongGlassCard(
                child: Row(
                  children: <Widget>[
                    Container(
                      width: 48,
                      height: 48,
                      alignment: Alignment.center,
                      decoration: const BoxDecoration(
                        shape: BoxShape.circle,
                        gradient: LinearGradient(
                          begin: Alignment.topLeft,
                          end: Alignment.bottomRight,
                          colors: <Color>[
                            EcomsbdColors.navyHeroTop,
                            EcomsbdColors.navy,
                          ],
                        ),
                      ),
                      child: Text(
                        _initials(shopName),
                        style: EcomsbdType.bodyStrong.copyWith(
                          color: Colors.white,
                        ),
                      ),
                    ),
                    const SizedBox(width: EcomsbdSpacing.md),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: <Widget>[
                          Text(shopName, style: EcomsbdType.sectionTitle),
                          const SizedBox(height: 2),
                          Text(
                            '$role · ${profile?.maskedPhone ?? ''}',
                            style: EcomsbdType.caption.copyWith(
                              color: EcomsbdColors.muted,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
              const _MenuSection(title: 'Operations', items: _operations),
              const _MenuSection(title: 'Money & data', items: _money),
              const _MenuSection(title: 'Account & growth', items: _account),
              const SizedBox(height: EcomsbdSpacing.lg),
              OutlinedButton.icon(
                onPressed: () async {
                  final navigator = Navigator.of(context);
                  await ref.read(authControllerProvider.notifier).signOut();
                  navigator.pop();
                },
                icon: const Icon(Icons.logout_rounded, size: 18),
                label: const Text('Sign out'),
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  foregroundColor: EcomsbdColors.red,
                  side: BorderSide(
                    color: EcomsbdColors.red.withValues(alpha: 0.25),
                  ),
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  static String _initials(String name) {
    final parts = name.trim().split(RegExp(r'\s+'));
    if (parts.length == 1) {
      return parts.first.characters.take(2).toString().toUpperCase();
    }
    return '${parts.first.characters.first}${parts[1].characters.first}'
        .toUpperCase();
  }
}

class _MenuSection extends StatelessWidget {
  const _MenuSection({required this.title, required this.items});

  final String title;
  final List<_MenuItem> items;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.fromLTRB(
            2,
            EcomsbdSpacing.lg,
            2,
            EcomsbdSpacing.sm,
          ),
          child: Text(
            title,
            style: EcomsbdType.bodyStrong.copyWith(color: EcomsbdColors.muted),
          ),
        ),
        ResponsiveGrid(
          minTileWidth: 160,
          maxColumns: 2,
          spacing: EcomsbdSpacing.sm,
          children: <Widget>[for (final item in items) _MenuTile(item: item)],
        ),
      ],
    );
  }
}

class _MenuTile extends StatelessWidget {
  const _MenuTile({required this.item});

  final _MenuItem item;

  @override
  Widget build(BuildContext context) {
    return Opacity(
      opacity: item.isAvailable ? 1 : 0.62,
      child: GlassCard(
        onTap: item.isAvailable ? () {} : null,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: EcomsbdColors.orangeSoft,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Icon(item.icon, size: 18, color: EcomsbdColors.orange),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              item.title,
              style: EcomsbdType.bodyStrong,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            const SizedBox(height: 3),
            Text(
              item.subtitle,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            if (!item.isAvailable) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.xs),
              StatusChip(
                label: item.phase!,
                tone: Tone.neutral,
                icon: Icons.schedule_rounded,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
