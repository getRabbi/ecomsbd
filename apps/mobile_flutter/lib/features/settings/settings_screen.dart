import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/billing/billing_providers.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/language_picker.dart';
import '../billing/plans_screen.dart';
import '../shared/responsive.dart';
import '../messaging/messaging_screen.dart';
import 'account_security_screen.dart';
import 'courier_accounts_screen.dart';
import 'data_privacy_screen.dart';
import 'notification_settings_screen.dart';
import 'integrations_screen.dart';
import 'order_sources_screen.dart';
import 'network_screen.dart';
import 'automation_screen.dart';

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
                EcomsbdLayout.pushedTopPadding,
                EcomsbdSpacing.page,
                EcomsbdSpacing.xxl,
              ),
              children: <Widget>[
                Row(
                  children: <Widget>[
                    IconButton(
                      onPressed: () => Navigator.of(context).maybePop(),
                      icon: const Icon(Icons.arrow_back_rounded),
                      tooltip: context.tr('common.back'),
                    ),
                    Expanded(
                      child: Text(
                        context.tr('settings.title'),
                        style: EcomsbdType.pageTitle,
                      ),
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
                              shop?.name ?? context.tr('common.yourShop'),
                              style: EcomsbdType.sectionTitle,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              context
                                  .tr('settings.signedInAs', <String, Object?>{
                                    'role': context.strings.role(
                                      auth.profile?.role ?? 'OWNER',
                                    ),
                                  }),
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

                SectionHeader(title: context.tr('settings.sectionLanguage')),
                const LanguageSettingRow(),
                ListTile(
                  leading: const Icon(Icons.auto_awesome),
                  title: Text(context.tr('automation.title')),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const AutomationScreen(),
                    ),
                  ),
                ),
                ListTile(
                  leading: const Icon(Icons.bar_chart),
                  title: Text(context.tr('network.title')),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const NetworkScreen(),
                    ),
                  ),
                ),
                ListTile(
                  leading: const Icon(Icons.hub_outlined),
                  title: Text(context.tr('int.title')),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const IntegrationsScreen(),
                    ),
                  ),
                ),
                ListTile(
                  leading: const Icon(Icons.input),
                  title: Text(context.tr('sources.title')),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const OrderSourcesScreen(),
                    ),
                  ),
                ),
                ListTile(
                  leading: const Icon(Icons.message_outlined),
                  title: Text(context.tr('messaging.title')),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const MessagingScreen(),
                    ),
                  ),
                ),

                SectionHeader(title: context.tr('settings.sectionPlan')),
                const _SubscriptionRow(),

                SectionHeader(title: context.tr('settings.sectionCouriers')),
                Consumer(
                  builder: (context, ref, _) {
                    final account = ref.watch(
                      courierAccountProvider(CourierAccountsScreen.steadfast),
                    );
                    return _SettingsRow(
                      icon: Icons.local_shipping_outlined,
                      title: context.tr('settings.courierAccounts'),
                      // The subtitle carries the state rather than a generic
                      // label: "needs reconnect" is something a seller has to
                      // act on, and burying it one tap deeper means booking
                      // fails before they find out.
                      subtitle: account.maybeWhen(
                        data: (value) => switch (value?.status) {
                          // "Steadfast" is a brand name and stays as it is in
                          // both languages.
                          CourierAccountStatus.connected => context.tr(
                            'settings.steadfastConnected',
                            <String, Object?>{
                              'identifier': value?.maskedIdentifier ?? '',
                            },
                          ).trim(),
                          CourierAccountStatus.needsReconnect => context.tr(
                            'settings.steadfastReconnect',
                          ),
                          // Not "connect Steadfast": the courier may be
                          // switched off for this shop, and the screen this
                          // opens is where each courier's real state is shown.
                          _ => context.tr('settings.courierDefault'),
                        },
                        orElse: () => context.tr('settings.courierDefault'),
                      ),
                      onTap: () => Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => const CourierAccountsScreen(),
                        ),
                      ),
                    );
                  },
                ),

                SectionHeader(title: context.tr('settings.sectionAccount')),
                _SettingsRow(
                  icon: Icons.devices_rounded,
                  title: context.tr('settings.devices'),
                  subtitle: context.tr('settings.devicesSub'),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const AccountSecurityScreen(),
                    ),
                  ),
                ),
                _SettingsRow(
                  icon: Icons.notifications_none_rounded,
                  title: context.tr('settings.notifications'),
                  subtitle: context.tr('settings.notificationsSub'),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const NotificationSettingsScreen(),
                    ),
                  ),
                ),
                _SettingsRow(
                  icon: Icons.shield_outlined,
                  title: context.tr('settings.privacy'),
                  subtitle: context.tr('settings.privacySub'),
                  onTap: () => Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const DataPrivacyScreen(),
                    ),
                  ),
                ),

                SectionHeader(title: context.tr('settings.sectionSync')),
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
                              isOffline
                                  ? context.tr('common.offline')
                                  : context.tr('common.upToDate'),
                              style: EcomsbdType.bodyStrong,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              pending.when(
                                data: (count) => count == 0
                                    ? context.tr('settings.everythingSynced')
                                    : context.trPlural(
                                        'settings.changesWaiting',
                                        count,
                                      ),
                                loading: () => context.tr('settings.checking'),
                                error: (_, __) =>
                                    context.tr('settings.couldNotReadQueue'),
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

                SectionHeader(title: context.tr('settings.sectionAbout')),
                GlassCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      _AboutRow(
                        label: context.tr('settings.appVersion'),
                        value: version ?? '—',
                      ),
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
      title: context.tr('settings.subscription'),
      subtitle: entitlements.when(
        data: (value) => switch (value.status) {
          // The status codes are API values and are matched, never shown.
          'GRACE' || 'PAST_DUE' => context.tr('settings.paymentProblem'),
          'CANCEL_AT_PERIOD_END' => context.tr('settings.endsSoon'),
          _ => context.tr('settings.planName', <String, Object?>{
            'plan': '${value.plan[0].toUpperCase()}${value.plan.substring(1)}',
          }),
        },
        loading: () => context.tr('common.loading'),
        // Never guess. A screen that says "Free" because the network failed is
        // telling the seller something that may not be true.
        error: (_, __) => context.tr('settings.couldNotCheckPlan'),
      ),
      trailing: entitlements.maybeWhen(
        data: (value) => value.status == 'GRACE' || value.status == 'PAST_DUE'
            ? StatusChip(
                label: context.tr('settings.actionNeeded'),
                tone: Tone.bad,
              )
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
              title: Text(context.tr('settings.signOutTitle')),
              // Said plainly: offline work is not lost, and a seller who has
              // just packed twelve parcels needs to know that before tapping.
              content: Text(context.tr('settings.signOutBody')),
              actions: <Widget>[
                TextButton(
                  onPressed: () => Navigator.of(context).pop(false),
                  child: Text(context.tr('common.cancel')),
                ),
                FilledButton(
                  onPressed: () => Navigator.of(context).pop(true),
                  child: Text(context.tr('common.signOut')),
                ),
              ],
            ),
          );
          if (confirmed ?? false) {
            await ref.read(authControllerProvider.notifier).signOut();
          }
        },
        icon: const Icon(Icons.logout_rounded, size: 18),
        label: Text(context.tr('common.signOut')),
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
              context.tr('settings.needsConnection', <String, Object?>{
                'what': what,
              }),
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
      return ConnectionRequiredNotice(what: context.tr('settings.thisWord'));
    }
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            error is ApiError
                ? (error as ApiError).displayMessage
                : context.tr('common.couldNotLoadThis'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (onRetry != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            TextButton(
              onPressed: onRetry,
              child: Text(context.tr('common.tryAgain')),
            ),
          ],
        ],
      ),
    );
  }
}
