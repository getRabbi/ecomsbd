import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Read where no `BuildContext` exists, so the active locale is resolved
/// directly -- the same approach `formatRelative` and `order_status.dart` use.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Courier accounts.
///
/// Brief sections 4 and 5. Three things this screen refuses to do:
///
/// * **Show a secret after it is saved.** There is no field for it, because
///   there is no response that carries one. The masked identifier is all a
///   seller gets back, and it is all they need to recognise which key is in.
/// * **Say "your key is wrong" when it does not know.** A courier outage and a
///   rejected credential look identical if you only have a boolean, so the four
///   validation outcomes are rendered as four different things.
/// * **Hide what the integration cannot do.** A courier with no documented
///   webhook says so, in plain words, next to the fact that status therefore
///   arrives by polling.
class CourierAccountsScreen extends ConsumerWidget {
  const CourierAccountsScreen({super.key});

  static const String steadfast = 'steadfast';

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final account = ref.watch(courierAccountProvider(steadfast));
    final evidence = ref.watch(providerEvidenceProvider(steadfast));

    return DetailScaffold(
      title: context.tr('ca.title'),
      children: <Widget>[
        account.when(
          loading: () => SkeletonLoader.card(height: 180),
          error: (error, _) => error is ApiError
              ? ErrorStateCard(
                  error: error,
                  onRetry: () =>
                      ref.invalidate(courierAccountProvider(steadfast)),
                )
              : EmptyState(
                  icon: Icons.error_outline,
                  title: context.tr('common.couldNotLoad'),
                  message: '$error',
                ),
          data: (value) => _SteadfastCard(account: value),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        evidence.when(
          loading: () => const SizedBox.shrink(),
          error: (_, __) => const SizedBox.shrink(),
          data: (value) => _WhatThisCourierSupports(evidence: value),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        const _ManualModeAlwaysWorks(),
      ],
    );
  }
}

class _SteadfastCard extends ConsumerStatefulWidget {
  const _SteadfastCard({required this.account});

  final CourierAccount? account;

  @override
  ConsumerState<_SteadfastCard> createState() => _SteadfastCardState();
}

class _SteadfastCardState extends ConsumerState<_SteadfastCard> {
  bool _busy = false;
  ConnectionTestResult? _lastCheck;

  Future<void> _test() async {
    setState(() {
      _busy = true;
      _lastCheck = null;
    });
    try {
      final result = await ref
          .read(courierRepositoryProvider)
          .testConnection(CourierAccountsScreen.steadfast);
      ref.invalidate(courierAccountsProvider);
      if (mounted) {
        setState(() => _lastCheck = result);
      }
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _disconnect() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('ca.disconnectTitle')),
        content: Text(context.tr('ca.disconnectBody')),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(context.tr('common.keepIt')),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(context.tr('common.disconnect')),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(courierRepositoryProvider)
          .disconnect(CourierAccountsScreen.steadfast);
      ref.invalidate(courierAccountsProvider);
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _connect() async {
    final result = await ConnectCourierSheet.show(
      context,
      provider: CourierAccountsScreen.steadfast,
      isReconnect: widget.account?.needsReconnect ?? false,
    );
    if (result != null && mounted) {
      ref.invalidate(courierAccountsProvider);
      setState(() => _lastCheck = result);
    }
  }

  @override
  Widget build(BuildContext context) {
    final account = widget.account;
    final status = account?.status ?? CourierAccountStatus.disconnected;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Expanded(
                child: Text('Steadfast', style: EcomsbdType.sectionTitle),
              ),
              StatusChip(label: status.label, tone: _toneFor(status)),
            ],
          ),
          const SizedBox(height: 3),
          Text(
            _subtitleFor(status),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (account?.maskedIdentifier != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _DetailRow(
              label: context.tr('ca.apiKeyLabel'),
              // The only credential-derived value that ever reaches this
              // device, and it is not reversible.
              value: account!.maskedIdentifier!,
            ),
          ],
          if (account?.lastVerifiedAt != null)
            _DetailRow(
              label: context.tr('ca.lastChecked'),
              value: _relative(account!.lastVerifiedAt!),
            ),
          if (account?.reportedBalancePaisa != null)
            _DetailRow(
              // Labelled as the courier's number, never merged with the COD
              // outstanding total on the Money screen: they measure different
              // things (brief section 19).
              label: context.tr('ca.reportedBalance'),
              value: Money(account!.reportedBalancePaisa!).format(),
            ),
          if (_lastCheck != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _CheckResultBanner(result: _lastCheck!),
          ],
          const SizedBox(height: EcomsbdSpacing.md),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              FilledButton(
                onPressed: _busy ? null : _connect,
                style: _primaryButton,
                child: Text(switch (status) {
                  CourierAccountStatus.connected => context.tr(
                    'ca.replaceKeys',
                  ),
                  CourierAccountStatus.needsReconnect => context.tr(
                    'ca.reconnect',
                  ),
                  _ => context.tr('common.connect'),
                }),
              ),
              if (account != null && account.connected)
                OutlinedButton(
                  onPressed: _busy ? null : _test,
                  child: Text(
                    _busy
                        ? context.tr('settings.checking')
                        : context.tr('ca.testConnection'),
                  ),
                ),
              if (account != null &&
                  status != CourierAccountStatus.disconnected)
                TextButton(
                  onPressed: _busy ? null : _disconnect,
                  child: Text(context.tr('common.disconnect')),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

Tone _toneFor(CourierAccountStatus status) => switch (status) {
  CourierAccountStatus.connected => Tone.good,
  CourierAccountStatus.needsReconnect => Tone.warning,
  CourierAccountStatus.disconnected => Tone.neutral,
  CourierAccountStatus.unknown => Tone.neutral,
};

String _subtitleFor(CourierAccountStatus status) => switch (status) {
  CourierAccountStatus.connected => _t('ca.connectedSub'),
  CourierAccountStatus.needsReconnect => _t('ca.needsReconnectSub'),
  CourierAccountStatus.disconnected => _t('ca.notConnectedSub'),
  CourierAccountStatus.unknown => _t('ca.unreadable'),
};

/// The four validation outcomes, rendered as four different things.
///
/// This is the whole point of the enum: "we could not check" must not look
/// like "your key is wrong". A seller who re-types a working key because the
/// courier had a bad minute has been failed by this widget.
class _CheckResultBanner extends StatelessWidget {
  const _CheckResultBanner({required this.result});

  final ConnectionTestResult result;

  @override
  Widget build(BuildContext context) {
    final (tone, title) = switch (result.result) {
      CredentialCheck.valid => (Tone.good, context.tr('provider.connected')),
      CredentialCheck.invalid => (Tone.bad, context.tr('ca.rejectedKeys')),
      CredentialCheck.providerUnavailable => (
        Tone.warning,
        context.tr('ca.noAnswer'),
      ),
      CredentialCheck.unknown => (Tone.warning, context.tr('ca.couldNotCheck')),
    };

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: tone.surface,
        borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(title, style: EcomsbdType.label.copyWith(color: tone.ink)),
          const SizedBox(height: 2),
          Text(result.message, style: EcomsbdType.caption),
        ],
      ),
    );
  }
}

/// What the courier's own documentation supports — and what it does not say.
class _WhatThisCourierSupports extends StatelessWidget {
  const _WhatThisCourierSupports({required this.evidence});

  final ProviderEvidence evidence;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('ca.supportsTitle'), style: EcomsbdType.label),
          const SizedBox(height: 3),
          Text(
            'Read from Steadfast API documentation '
            '${evidence.documentationVersion ?? ''}. Anything not listed there '
            'is not guessed at.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              for (final entry in _shownCapabilities)
                if (evidence.capabilities[entry.$1] == 'true')
                  StatusChip(label: context.tr(entry.$2), tone: Tone.good),
            ],
          ),
          if (evidence.hasNoWebhook) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.sync_rounded, size: 15, color: EcomsbdColors.muted2),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    context.tr('ca.pollingNote'),
                    style: EcomsbdType.caption,
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

/// Capability flag from the provider manifest, paired with the key its label
/// lives under. Resolved where rendered: a const list cannot hold a lookup.
const List<(String, String)> _shownCapabilities = <(String, String)>[
  ('create_single', 'ca.capBookParcels'),
  ('create_bulk', 'ca.capBookBulk'),
  ('status_lookup', 'ca.capTrackStatus'),
  ('returns', 'ca.capRequestReturns'),
  ('payments', 'ca.capImportPayments'),
  ('balance', 'ca.capAccountBalance'),
];

class _ManualModeAlwaysWorks extends StatelessWidget {
  const _ManualModeAlwaysWorks();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('ca.manualTitle'), style: EcomsbdType.label),
          const SizedBox(height: 3),
          Text(
            context.tr('ca.manualBody'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

/// The one form in the app that carries a secret.
class ConnectCourierSheet extends ConsumerStatefulWidget {
  const ConnectCourierSheet({
    required this.provider,
    super.key,
    this.isReconnect = false,
  });

  final String provider;
  final bool isReconnect;

  static Future<ConnectionTestResult?> show(
    BuildContext context, {
    required String provider,
    bool isReconnect = false,
  }) {
    return showModalBottomSheet<ConnectionTestResult>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) =>
          ConnectCourierSheet(provider: provider, isReconnect: isReconnect),
    );
  }

  @override
  ConsumerState<ConnectCourierSheet> createState() =>
      _ConnectCourierSheetState();
}

class _ConnectCourierSheetState extends ConsumerState<ConnectCourierSheet> {
  final TextEditingController _apiKey = TextEditingController();
  final TextEditingController _secretKey = TextEditingController();
  bool _busy = false;
  ApiError? _error;

  @override
  void dispose() {
    _apiKey.dispose();
    _secretKey.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ref
          .read(courierRepositoryProvider)
          .connect(
            provider: widget.provider,
            apiKey: _apiKey.text.trim(),
            secretKey: _secretKey.text.trim(),
          );
      // Cleared immediately: the values have left the device and there is no
      // reason for them to stay in a controller behind a dismissed sheet.
      _apiKey.clear();
      _secretKey.clear();
      if (mounted) {
        Navigator.of(context).pop(result);
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;
    final canSubmit =
        _apiKey.text.trim().isNotEmpty && _secretKey.text.trim().isNotEmpty;

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      child: Container(
        decoration: const BoxDecoration(
          color: EcomsbdColors.backgroundLight,
          borderRadius: BorderRadius.vertical(
            top: Radius.circular(EcomsbdRadii.lg),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(
          EcomsbdSpacing.lg,
          EcomsbdSpacing.md,
          EcomsbdSpacing.lg,
          EcomsbdSpacing.xl,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            const _SheetGrip(),
            const SizedBox(height: EcomsbdSpacing.md),
            Text(
              widget.isReconnect
                  ? context.tr('ca.reconnectSteadfast')
                  : context.tr('ca.connectSteadfast'),
              style: EcomsbdType.sectionTitle,
            ),
            const SizedBox(height: 3),
            Text(
              context.tr('ca.keysNote'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: context.tr('ca.apiKeyField'),
              controller: _apiKey,
              hint: context.tr('ca.apiKeyHint'),
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            LabelledField(
              label: context.tr('ca.secretKeyField'),
              controller: _secretKey,
              hint: context.tr('ca.secretKeyHint'),
              obscureText: true,
              onChanged: (_) => setState(() {}),
            ),
            if (_error != null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              Text(
                _error!.displayMessage,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
              ),
            ],
            const SizedBox(height: EcomsbdSpacing.md),
            FilledButton(
              onPressed: _busy || !canSubmit ? null : _submit,
              style: _primaryButton,
              child: Text(
                _busy
                    ? context.tr('ca.checkingWith')
                    : context.tr('ca.saveAndCheck'),
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.lock_outline, size: 14, color: EcomsbdColors.muted2),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    context.tr('ca.readOnlyNote'),
                    style: EcomsbdType.caption,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          Text(value, style: EcomsbdType.label),
        ],
      ),
    );
  }
}

class _SheetGrip extends StatelessWidget {
  const _SheetGrip();

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Container(
        width: 40,
        height: 4,
        decoration: BoxDecoration(
          color: EcomsbdColors.trackLight,
          borderRadius: BorderRadius.circular(2),
        ),
      ),
    );
  }
}

String _relative(DateTime at) {
  final delta = DateTime.now().toUtc().difference(at.toUtc());
  if (delta.inMinutes < 1) {
    return 'just now';
  }
  if (delta.inHours < 1) {
    return '${delta.inMinutes}m ago';
  }
  if (delta.inDays < 1) {
    return '${delta.inHours}h ago';
  }
  return '${delta.inDays}d ago';
}

final ButtonStyle _primaryButton = FilledButton.styleFrom(
  backgroundColor: EcomsbdColors.orange,
  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
  shape: const StadiumBorder(),
  textStyle: EcomsbdType.label,
);
