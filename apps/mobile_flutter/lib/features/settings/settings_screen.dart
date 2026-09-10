import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/env.dart';
import '../../data/billing/billing_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../billing/plans_screen.dart';
import '../shared/responsive.dart';
import 'account_security_screen.dart';
import 'data_privacy_screen.dart';
import 'notification_settings_screen.dart';

/// Settings.
///
/// Master spec sections 89, 99, 100, 101. Everything here is either something
/// a seller controls about their own account, or something they are entitled to
/// know about it — which devices are signed in, what leaves the product, what
/// happens if they leave.
///
/// Nothing on this screen is decorative. Each row goes somewhere that does
/// something, or it is not here.
class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final version = ref.watch(appVersionProvider);
    final pending = ref.watch(pendingMutationCountProvider);
    final isOffline = ref.watch(isOfflineProvider);
    final shop = auth.profile?.activeTenant;

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(
                EcomsbdSpacing.page,
                0,
                EcomsbdSpacing.page,
                EcomsbdSpacing.xxl,
              ),
              children: <Widget>[
                Row(
                  children: <Widget>[
                    IconButton(
                      onPressed: () => Navigator.of(context).maybePop(),
                      icon: const Icon(Icons.arrow_back_rounded),
                      tooltip: 'Back',
                    ),
                    const Expanded(
                      child: Text('Settings', style: EcomsbdType.pageTitle),
                    ),
                  ],
                ),

                // Shop identity, so the seller can see which shop these
                // settings belong to before they change one.
                GlassCard(
                  child: Row(
                    children: <Widget>[
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              shop?.name ?? 'Your shop',
                              style: EcomsbdType.sectionTitle,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              'Signed in as ${auth.profile?.role ?? 'OWNER'}',
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

                const SectionHeader(title: 'Plan'),
                const _SubscriptionRow(),

                const SectionHeader(title: 'Account'),
                _SettingsRow(
                  icon: Icons.devices_rounded,
                  title: 'Devices and sessions',
                  subtitle: 'See where you are signed in, and sign out',
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const AccountSecurityScreen(),
                    ),
                  ),
                ),
                _SettingsRow(
                  icon: Icons.notifications_none_rounded,
                  title: 'Notifications',
                  subtitle: 'What we interrupt you about',
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const NotificationSettingsScreen(),
                    ),
                  ),
                ),
                _SettingsRow(
                  icon: Icons.shield_outlined,
                  title: 'Your data and privacy',
                  subtitle: 'Export everything, or close your account',
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const DataPrivacyScreen(),
                    ),
                  ),
                ),

                const SectionHeader(title: 'Sync'),
                GlassCard(
                  child: Row(
                    children: <Widget>[
                      Icon(
                        isOffline
                            ? Icons.cloud_off_rounded
                            : Icons.cloud_done_rounded,
                        size: 18,
                        color: isOffline ? Tone.warning.ink : Tone.good.ink,
                      ),
                      const SizedBox(width: EcomsbdSpacing.sm),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(
                              isOffline ? 'Offline' : 'Up to date',
                              style: EcomsbdType.bodyStrong,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              pending.when(
                                data: (count) => count == 0
                                    ? 'Everything on this phone has reached the server.'
                                    : '$count change${count == 1 ? '' : 's'} '
                                          'waiting to sync. Nothing is lost.',
                                loading: () => 'Checking…',
                                error: (_, __) => 'Could not read the queue.',
                              ),
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

                const SectionHeader(title: 'About'),
                GlassCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      _AboutRow(label: 'App version', value: version ?? '—'),
                      const SizedBox(height: 6),
                      // Named so a support conversation can start with "which
                      // server are you on?" rather than guessing.
                      const _AboutRow(label: 'Server', value: Env.apiBaseUrl),
                    ],
                  ),
                ),

                const SizedBox(height: EcomsbdSpacing.md),
                _SignOutButton(),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SubscriptionRow extends ConsumerWidget {
  const _SubscriptionRow();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final entitlements = ref.watch(entitlementsProvider);

    return _SettingsRow(
      icon: Icons.star_outline_rounded,
      title: 'Subscription',
      subtitle: entitlements.when(
        data: (value) => switch (value.status) {
          'GRACE' || 'PAST_DUE' => 'Payment problem — tap to fix',
          'CANCEL_AT_PERIOD_END' => 'Ends soon',
          _ => '${value.plan[0].toUpperCase()}${value.plan.substring(1)} plan',
        },
        loading: () => 'Loading…',
        // Never guess. A screen that says "Free" because the network failed is
        // telling the seller something that may not be true.
        error: (_, __) => 'Could not check your plan',
      ),
      trailing: entitlements.maybeWhen(
        data: (value) => value.status == 'GRACE' || value.status == 'PAST_DUE'
            ? const StatusChip(label: 'Action needed', tone: Tone.bad)
            : null,
        orElse: () => null,
      ),
      onTap: () => Navigator.of(
        context,
      ).push(MaterialPageRoute<void>(builder: (_) => const PlansScreen())),
    );
  }
}

class _SettingsRow extends StatelessWidget {
  const _SettingsRow({
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onTap,
    this.trailing,
  });

  final IconData icon;
  final String title;
  final String subtitle;
  final VoidCallback onTap;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      onTap: onTap,
      child: Row(
        children: <Widget>[
          Icon(icon, size: 20, color: EcomsbdColors.muted2),
          const SizedBox(width: EcomsbdSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(title, style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          if (trailing != null) ...<Widget>[
            const SizedBox(width: EcomsbdSpacing.sm),
            trailing!,
          ],
          const SizedBox(width: 4),
          const Icon(
            Icons.chevron_right_rounded,
            size: 20,
            color: EcomsbdColors.muted2,
          ),
        ],
      ),
    );
  }
}

class _AboutRow extends StatelessWidget {
  const _AboutRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        Expanded(
          child: Text(
            label,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ),
        Flexible(
          child: Text(
            value,
            style: EcomsbdType.chip,
            textAlign: TextAlign.right,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }
}

class _SignOutButton extends ConsumerWidget {
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton.icon(
        onPressed: () async {
          final confirmed = await showDialog<bool>(
            context: context,
            builder: (context) => AlertDialog(
              title: const Text('Sign out of this device?'),
              // Said plainly: offline work is not lost, and a seller who has
              // just packed twelve parcels needs to know that before tapping.
              content: const Text(
                'Anything waiting to sync will be sent first. You can sign '
                'back in with the same number.',
              ),
              actions: <Widget>[
                TextButton(
                  onPressed: () => Navigator.of(context).pop(false),
                  child: const Text('Cancel'),
                ),
                FilledButton(
                  onPressed: () => Navigator.of(context).pop(true),
                  child: const Text('Sign out'),
                ),
              ],
            ),
          );
          if (confirmed ?? false) {
            await ref.read(authControllerProvider.notifier).signOut();
          }
        },
        icon: const Icon(Icons.logout_rounded, size: 18),
        label: const Text('Sign out'),
        style: OutlinedButton.styleFrom(
          minimumSize: const Size(0, EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
        ),
      ),
    );
  }
}

/// Shown when a screen needs a connection and does not have one.
class ConnectionRequiredNotice extends StatelessWidget {
  const ConnectionRequiredNotice({required this.what, super.key});

  final String what;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.cloud_off_rounded, size: 18, color: Tone.warning.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              '$what needs a connection. Nothing is saved on this phone for '
              'it, because a stale answer here could be wrong.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
      ),
    );
  }
}

/// One failure, rendered the same way on every settings screen.
class SettingsError extends StatelessWidget {
  const SettingsError({required this.error, super.key, this.onRetry});

  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    if (error is ApiError && (error as ApiError).isOffline) {
      return const ConnectionRequiredNotice(what: 'This');
    }
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            error is ApiError
                ? (error as ApiError).displayMessage
                : 'Could not load this.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (onRetry != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            TextButton(onPressed: onRetry, child: const Text('Try again')),
          ],
        ],
      ),
    );
  }
}
