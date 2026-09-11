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
      title: 'Courier accounts',
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
                  title: 'Could not load',
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
        title: const Text('Disconnect Steadfast?'),
        content: const Text(
          'Your API key and secret key are erased. Parcels already booked keep '
          'their tracking, and you can still record couriers by hand.',
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('Disconnect'),
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
              label: 'API key',
              // The only credential-derived value that ever reaches this
              // device, and it is not reversible.
              value: account!.maskedIdentifier!,
            ),
          ],
          if (account?.lastVerifiedAt != null)
            _DetailRow(
              label: 'Last checked',
              value: _relative(account!.lastVerifiedAt!),
            ),
          if (account?.reportedBalancePaisa != null)
            _DetailRow(
              // Labelled as the courier's number, never merged with the COD
              // outstanding total on the Money screen: they measure different
              // things (brief section 19).
              label: 'Steadfast reported balance',
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
                  CourierAccountStatus.connected => 'Replace keys',
                  CourierAccountStatus.needsReconnect => 'Reconnect',
                  _ => 'Connect',
                }),
              ),
              if (account != null && account.connected)
                OutlinedButton(
                  onPressed: _busy ? null : _test,
                  child: Text(_busy ? 'Checking…' : 'Test connection'),
                ),
              if (account != null &&
                  status != CourierAccountStatus.disconnected)
                TextButton(
                  onPressed: _busy ? null : _disconnect,
                  child: const Text('Disconnect'),
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
  CourierAccountStatus.connected =>
    'Book parcels, sync their status and import payments automatically.',
  CourierAccountStatus.needsReconnect =>
    'Steadfast stopped accepting these keys. Enter them again to keep booking.',
  CourierAccountStatus.disconnected =>
    'Connect your merchant API key to book parcels from ecomsbd.',
  CourierAccountStatus.unknown => 'We could not read this account.',
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
      CredentialCheck.valid => (Tone.good, 'Connected'),
      CredentialCheck.invalid => (Tone.bad, 'Steadfast rejected these keys'),
      CredentialCheck.providerUnavailable => (
        Tone.warning,
        'Steadfast did not answer',
      ),
      CredentialCheck.unknown => (Tone.warning, 'Could not check'),
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
          const Text('What Steadfast supports', style: EcomsbdType.label),
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
                  StatusChip(label: entry.$2, tone: Tone.good),
            ],
          ),
          if (evidence.hasNoWebhook) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.sync_rounded, size: 15, color: EcomsbdColors.muted2),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Steadfast does not publish a callback, so ecomsbd checks '
                    'each parcel on a schedule instead. Status still stays up '
                    'to date — it just arrives a few minutes later.',
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

const List<(String, String)> _shownCapabilities = <(String, String)>[
  ('create_single', 'Book parcels'),
  ('create_bulk', 'Book in bulk'),
  ('status_lookup', 'Track status'),
  ('returns', 'Request returns'),
  ('payments', 'Import payments'),
  ('balance', 'Account balance'),
];

class _ManualModeAlwaysWorks extends StatelessWidget {
  const _ManualModeAlwaysWorks();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text('Manual courier mode', style: EcomsbdType.label),
          const SizedBox(height: 3),
          Text(
            'Whether or not a courier is connected, you can record a parcel and '
            'its tracking code by hand and upload the courier payout statement. '
            'Connecting Steadfast adds to that — it never replaces it.',
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
              widget.isReconnect ? 'Reconnect Steadfast' : 'Connect Steadfast',
              style: EcomsbdType.sectionTitle,
            ),
            const SizedBox(height: 3),
            Text(
              'Find these in your Steadfast merchant panel. They are stored '
              'encrypted and are never shown again after you save them.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: 'API Key',
              controller: _apiKey,
              hint: 'From the Steadfast merchant panel',
              onChanged: (_) => setState(() {}),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            LabelledField(
              label: 'Secret Key',
              controller: _secretKey,
              hint: 'Kept encrypted, never displayed again',
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
                _busy ? 'Checking with Steadfast…' : 'Save and check',
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
            const Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Icon(Icons.lock_outline, size: 14, color: EcomsbdColors.muted2),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'We check the keys with Steadfast before saving them, using '
                    'a read-only call. No parcel is created.',
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
