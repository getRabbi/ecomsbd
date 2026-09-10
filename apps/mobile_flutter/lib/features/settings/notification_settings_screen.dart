import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/account/models.dart';
import '../../data/billing/billing_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/responsive.dart';
import 'settings_screen.dart' show SettingsError;

/// What the product is allowed to interrupt the seller about.
///
/// Master spec section 95. The screen states its own governing rule, because
/// a seller deciding whether to allow notifications deserves to know we are
/// not going to send one per parcel event: everything lands in the
/// notification centre either way, and push is only the second copy.
///
/// A switch for a transport that does not exist in this deployment is shown
/// disabled with the reason — better than a control that changes nothing.
class NotificationSettingsScreen extends ConsumerWidget {
  const NotificationSettingsScreen({super.key});

  static const List<({String kind, String label})> _kinds =
      <({String kind, String label})>[
        (kind: 'DELIVERED_BUT_UNPAID', label: 'Delivered but not paid'),
        (kind: 'UNDERPAID', label: 'Paid less than expected'),
        (kind: 'STALE_IN_TRANSIT', label: 'Parcels stuck in transit'),
        (
          kind: 'RETURNED_NOT_RESTOCKED',
          label: 'Returns not put back in stock',
        ),
        (kind: 'RETURN_SPIKE', label: 'More returns than usual'),
        (kind: 'WEEKLY_SUMMARY', label: 'Friday summary'),
      ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final preferences = ref.watch(notificationPreferencesProvider);

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
                      child: Text(
                        'Notifications',
                        style: EcomsbdType.pageTitle,
                      ),
                    ),
                  ],
                ),

                GlassCard(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      Icon(Icons.inbox_rounded, size: 18, color: Tone.info.ink),
                      const SizedBox(width: EcomsbdSpacing.sm),
                      Expanded(
                        child: Text(
                          'Everything we raise is always in your notification '
                          'centre, whatever you switch off here. These settings '
                          'only decide what interrupts you.',
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),

                preferences.when(
                  loading: () => const Padding(
                    padding: EdgeInsets.all(EcomsbdSpacing.lg),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                  error: (error, _) => SettingsError(
                    error: error,
                    onRetry: () =>
                        ref.invalidate(notificationPreferencesProvider),
                  ),
                  data: (value) => _Form(preferences: value, kinds: _kinds),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _Form extends ConsumerStatefulWidget {
  const _Form({required this.preferences, required this.kinds});

  final NotificationPreferences preferences;
  final List<({String kind, String label})> kinds;

  @override
  ConsumerState<_Form> createState() => _FormState();
}

class _FormState extends ConsumerState<_Form> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final preferences = widget.preferences;

    return Column(
      children: <Widget>[
        const SectionHeader(title: 'Channels'),
        GlassCard(
          child: Column(
            children: <Widget>[
              _ChannelSwitch(
                title: 'Push notifications',
                subtitle: preferences.pushTransportAvailable
                    ? 'Money alerts on this phone'
                    : 'Not switched on yet in this app',
                value: preferences.pushEnabled,
                enabled: preferences.pushTransportAvailable && !_busy,
                onChanged: (value) => _save(pushEnabled: value),
              ),
              const Divider(height: EcomsbdSpacing.lg),
              _ChannelSwitch(
                title: 'SMS to customers',
                subtitle: preferences.smsTransportAvailable
                    ? 'Order and delivery updates. Uses your SMS allowance.'
                    : 'Not switched on yet — no SMS provider is connected',
                value: preferences.smsEnabled,
                enabled: preferences.smsTransportAvailable && !_busy,
                onChanged: (value) => _save(smsEnabled: value),
              ),
            ],
          ),
        ),

        const SectionHeader(
          title: 'Parcel tracking',
          subtitle: 'Off by design — one push per scan is noise',
        ),
        GlassCard(
          child: _ChannelSwitch(
            title: 'Every tracking update',
            subtitle:
                'Sends a notification each time a parcel moves. Most sellers '
                'find this too much; the alerts that need you are always sent.',
            value: preferences.routineTrackingPush,
            enabled: preferences.pushTransportAvailable && !_busy,
            onChanged: (value) => _save(routineTrackingPush: value),
          ),
        ),

        const SectionHeader(title: 'What we tell you about'),
        GlassCard(
          child: Column(
            children: <Widget>[
              for (var index = 0; index < widget.kinds.length; index++) ...[
                if (index > 0) const Divider(height: EcomsbdSpacing.lg),
                _ChannelSwitch(
                  title: widget.kinds[index].label,
                  subtitle: null,
                  value: !preferences.mutedKinds.contains(
                    widget.kinds[index].kind,
                  ),
                  enabled: !_busy,
                  onChanged: (value) =>
                      _toggleKind(widget.kinds[index].kind, muted: !value),
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _toggleKind(String kind, {required bool muted}) {
    final next = <String>{...widget.preferences.mutedKinds};
    if (muted) {
      next.add(kind);
    } else {
      next.remove(kind);
    }
    return _save(mutedKinds: next.toList());
  }

  Future<void> _save({
    bool? pushEnabled,
    bool? smsEnabled,
    bool? routineTrackingPush,
    List<String>? mutedKinds,
  }) async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await ref
          .read(accountRepositoryProvider)
          .updateNotificationPreferences(
            pushEnabled: pushEnabled,
            smsEnabled: smsEnabled,
            routineTrackingPush: routineTrackingPush,
            mutedKinds: mutedKinds,
          );
      ref.invalidate(notificationPreferencesProvider);
    } on ApiError catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.displayMessage)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

class _ChannelSwitch extends StatelessWidget {
  const _ChannelSwitch({
    required this.title,
    required this.subtitle,
    required this.value,
    required this.enabled,
    required this.onChanged,
  });

  final String title;
  final String? subtitle;
  final bool value;
  final bool enabled;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                title,
                style: EcomsbdType.bodyStrong.copyWith(
                  color: enabled ? null : EcomsbdColors.muted2,
                ),
              ),
              if (subtitle != null) ...<Widget>[
                const SizedBox(height: 2),
                Text(
                  subtitle!,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(width: EcomsbdSpacing.sm),
        Switch(value: value && enabled, onChanged: enabled ? onChanged : null),
      ],
    );
  }
}
