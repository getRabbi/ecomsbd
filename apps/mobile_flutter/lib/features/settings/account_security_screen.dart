import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/account/models.dart';
import '../../data/billing/billing_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart' show formatRelative;
import '../shared/responsive.dart';
import 'settings_screen.dart' show SettingsError;
import '../../l10n/app_strings.dart';

/// Devices and sessions (master spec section 89).
///
/// The screen a seller opens when they have lost a phone. Two things follow
/// from that: it must be *current* — so nothing here is cached — and the
/// destructive action must be unmistakable about which device it ends.
class AccountSecurityScreen extends ConsumerWidget {
  const AccountSecurityScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final devices = ref.watch(devicesProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              edgeOffset: EcomsbdLayout.pushedRefreshOffset,
              onRefresh: () async => ref.refresh(devicesProvider.future),
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
                          context.tr('as.devices'),
                          style: EcomsbdType.pageTitle,
                        ),
                      ),
                    ],
                  ),
                  Padding(
                    padding: const EdgeInsets.only(
                      left: EcomsbdSpacing.xs,
                      bottom: EcomsbdSpacing.md,
                    ),
                    child: Text(
                      context.tr('as.devicesBody'),
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ),

                  devices.when(
                    loading: () => const Padding(
                      padding: EdgeInsets.all(EcomsbdSpacing.lg),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                    error: (error, _) => SettingsError(
                      error: error,
                      onRetry: () => ref.invalidate(devicesProvider),
                    ),
                    data: (rows) => Column(
                      children: <Widget>[
                        for (final device in rows) _DeviceCard(device: device),
                      ],
                    ),
                  ),

                  const SizedBox(height: EcomsbdSpacing.md),
                  _LogoutOthersButton(),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _DeviceCard extends ConsumerWidget {
  const _DeviceCard({required this.device});

  final DeviceInfo device;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GlassCard(
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(
            device.platform == 'ANDROID'
                ? Icons.phone_android_rounded
                : Icons.devices_other_rounded,
            size: 20,
            color: EcomsbdColors.muted2,
          ),
          const SizedBox(width: EcomsbdSpacing.md),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Flexible(
                      child: Text(
                        device.displayName,
                        style: EcomsbdType.bodyStrong,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    if (device.isCurrent) ...<Widget>[
                      const SizedBox(width: EcomsbdSpacing.xs),
                      StatusChip(
                        label: context.tr('as.thisDevice'),
                        tone: Tone.good,
                      ),
                    ],
                    if (device.revoked) ...<Widget>[
                      const SizedBox(width: EcomsbdSpacing.xs),
                      StatusChip(
                        label: context.tr('as.signedOut'),
                        tone: Tone.neutral,
                      ),
                    ],
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  context.tr('as.lastUsed', <String, Object?>{
                        'when': formatRelative(device.lastSeenAt),
                      }) +
                      (device.appVersion == null
                          ? ''
                          : ' · v${device.appVersion}'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
                if (device.pushEnabled) ...<Widget>[
                  const SizedBox(height: 2),
                  Text(
                    context.tr('as.receivesNotifications'),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (!device.revoked && !device.isCurrent)
            TextButton(
              onPressed: () => _revoke(context, ref),
              style: TextButton.styleFrom(
                foregroundColor: Tone.bad.ink,
                minimumSize: const Size(0, EcomsbdTouch.minTarget),
              ),
              child: Text(context.tr('common.signOut')),
            ),
        ],
      ),
    );
  }

  Future<void> _revoke(BuildContext context, WidgetRef ref) async {
    final messenger = ScaffoldMessenger.of(context);
    final strings = context.strings;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          context.tr('sec.signOutDevice', <String, Object?>{
            'device': device.displayName,
          }),
        ),
        content: Text(context.tr('as.signOutOneBody')),
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
    if (!(confirmed ?? false)) return;

    try {
      await ref.read(accountRepositoryProvider).revokeDevice(device.id);
      ref.invalidate(devicesProvider);
      messenger.showSnackBar(
        SnackBar(content: Text(strings.t('as.signedOutDone'))),
      );
    } on ApiError catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.displayMessage)));
    }
  }
}

class _LogoutOthersButton extends ConsumerStatefulWidget {
  @override
  ConsumerState<_LogoutOthersButton> createState() =>
      _LogoutOthersButtonState();
}

class _LogoutOthersButtonState extends ConsumerState<_LogoutOthersButton> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton.icon(
        onPressed: _busy ? null : _run,
        icon: const Icon(Icons.logout_rounded, size: 18),
        label: Text(
          _busy ? context.tr('as.signingOut') : context.tr('as.signOutOthers'),
        ),
        style: OutlinedButton.styleFrom(
          foregroundColor: Tone.bad.ink,
          minimumSize: const Size(0, EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
        ),
      ),
    );
  }

  Future<void> _run() async {
    final messenger = ScaffoldMessenger.of(context);
    final strings = context.strings;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('as.signOutOthersTitle')),
        // The reassurance a seller needs before tapping the scary button:
        // this device keeps working.
        content: Text(context.tr('as.signOutOthersBody')),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(context.tr('common.cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(context.tr('as.signThemOut')),
          ),
        ],
      ),
    );
    if (!(confirmed ?? false)) return;

    setState(() => _busy = true);
    try {
      await ref.read(accountRepositoryProvider).logoutOtherDevices();
      ref.invalidate(devicesProvider);
      messenger.showSnackBar(
        SnackBar(content: Text(strings.t('as.othersSignedOut'))),
      );
    } on ApiError catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.displayMessage)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
