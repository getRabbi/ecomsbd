import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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
    final evidence = ref.watch(providerEvidenceProvider(steadfast));

    return DetailScaffold(
      title: context.tr('ca.title'),
      children: <Widget>[
        // Every courier, rendered from the server's own declaration of how to
        // connect it. There is no per-courier widget any more: adding RedX,
        // once its documentation exists, needs no change in this file.
        const _Couriers(),
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

/// Every connectable courier, driven entirely by `/couriers/providers`.
///
/// Couriers with nothing to connect — manual mode, or one with no verified
/// contract — are absent from the server's list, so they are absent here
/// without this widget knowing why.
class _Couriers extends ConsumerWidget {
  const _Couriers();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final providers = ref.watch(courierProvidersProvider);
    return providers.when(
      loading: () => SkeletonLoader.card(height: 180),
      error: (error, _) => error is ApiError
          ? ErrorStateCard(
              error: error,
              onRetry: () => ref.invalidate(courierProvidersProvider),
            )
          : EmptyState(
              icon: Icons.error_outline,
              title: context.tr('common.couldNotLoad'),
              message: '$error',
            ),
      data: (rows) {
        final connectable = <CourierProviderInfo>[
          for (final row in rows)
            if (row.isConnectable) row,
        ];
        if (connectable.isEmpty) {
          return const SizedBox.shrink();
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            for (final info in connectable) ...<Widget>[
              _ProviderCard(info: info),
              const SizedBox(height: EcomsbdSpacing.md),
            ],
          ],
        );
      },
    );
  }
}

Tone _toneFor(CourierAccountStatus status) => switch (status) {
  CourierAccountStatus.connected => Tone.good,
  CourierAccountStatus.needsReconnect => Tone.warning,
  CourierAccountStatus.disconnected => Tone.neutral,
  CourierAccountStatus.unknown => Tone.neutral,
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

/// A courier card driven entirely by the server's declaration.
///
/// Nothing in here names a courier. The fields to ask for, whether a pickup
/// store is needed, whether there is a sandbox and whether callbacks are
/// verified all come from `/couriers/providers`, so this one widget serves
/// Pathao today and RedX next without changing.
///
/// Two states it treats as first-class, because for Pathao they are not edge
/// cases:
///
/// * **Connected but not bookable.** Pathao's `store_id` is mandatory on every
///   booking and Pathao supplies no default, so an account with valid
///   credentials and no pickup store cannot book. That is shown as its own
///   state with the action that fixes it, rather than left to fail later at
///   booking time.
/// * **Connected but not receiving updates.** Pathao publishes no status
///   lookup, so without a callback a parcel's status never advances. The card
///   says so and hands over the URL.
class _ProviderCard extends ConsumerStatefulWidget {
  const _ProviderCard({required this.info});

  final CourierProviderInfo info;

  @override
  ConsumerState<_ProviderCard> createState() => _ProviderCardState();
}

class _ProviderCardState extends ConsumerState<_ProviderCard> {
  bool _busy = false;
  ConnectionTestResult? _lastCheck;

  String get _provider => widget.info.provider;
  ProviderConnectForm get _form => widget.info.connectForm!;

  Future<void> _guard(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
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

  Future<void> _connect(CourierAccount? account) async {
    final result = await ConnectCourierSheet.show(
      context,
      provider: _provider,
      isReconnect: account?.needsReconnect ?? false,
      form: _form,
      sandbox: account?.sandbox ?? false,
    );
    if (result != null && mounted) {
      ref.invalidate(courierAccountsProvider);
      ref.invalidate(webhookSetupProvider(_provider));
      setState(() => _lastCheck = result);
    }
  }

  Future<void> _test() async {
    setState(() => _lastCheck = null);
    await _guard(() async {
      final result = await ref
          .read(courierRepositoryProvider)
          .testConnection(_provider);
      ref.invalidate(courierAccountsProvider);
      if (mounted) {
        setState(() => _lastCheck = result);
      }
    });
  }

  Future<void> _disconnect() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(
          context.tr('ca.disconnectProviderTitle', <String, Object?>{
            'provider': widget.info.displayName,
          }),
        ),
        content: Text(context.tr('ca.disconnectProviderBody')),
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
    await _guard(() async {
      await ref.read(courierRepositoryProvider).disconnect(_provider);
      ref.invalidate(courierAccountsProvider);
      ref.invalidate(webhookSetupProvider(_provider));
    });
  }

  Future<void> _chooseStore() async {
    final chosen = await _StorePickerSheet.show(
      context,
      provider: _provider,
      displayName: widget.info.displayName,
    );
    if (chosen == null) {
      return;
    }
    await _guard(() async {
      await ref
          .read(courierRepositoryProvider)
          .selectStore(
            _provider,
            providerStoreId: chosen.providerStoreId,
            name: chosen.name,
          );
      ref.invalidate(courierAccountsProvider);
    });
  }

  @override
  Widget build(BuildContext context) {
    final account = ref.watch(courierAccountProvider(_provider)).value;
    final status = account?.status ?? CourierAccountStatus.disconnected;
    final connected = account?.connected ?? false;
    final needsStore = _form.requiresStore && connected && account?.storeId == null;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  widget.info.displayName,
                  style: EcomsbdType.sectionTitle,
                ),
              ),
              if (account?.sandbox ?? false) ...<Widget>[
                StatusChip(
                  label: context.tr('ca.sandboxChip'),
                  tone: Tone.warning,
                ),
                const SizedBox(width: EcomsbdSpacing.xs),
              ],
              StatusChip(label: status.label, tone: _toneFor(status)),
            ],
          ),
          const SizedBox(height: 3),
          Text(
            // "Connected" is not the whole truth when a mandatory pickup store
            // is missing, so this says what is actually blocking a booking.
            needsStore
                ? context.tr('ca.needsStoreSub')
                : _providerSubtitle(status, widget.info.displayName),
            style: EcomsbdType.caption.copyWith(
              color: needsStore ? EcomsbdColors.red : EcomsbdColors.muted,
            ),
          ),
          if (account?.maskedIdentifier != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _DetailRow(
              label: _form.fields.isEmpty
                  ? context.tr('ca.apiKeyLabel')
                  : _form.fields.first.label,
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
              // Labelled as the courier's own number, never merged with the
              // COD outstanding total on the Money screen: they measure
              // different things (brief section 19).
              label: context.tr('ca.providerReportedBalance', <String, Object?>{
                'provider': widget.info.displayName,
              }),
              value: Money(account!.reportedBalancePaisa!).format(),
            ),
          if (_form.requiresStore && connected)
            _DetailRow(
              label: context.tr('ca.pickupStore'),
              value: account?.storeName ??
                  account?.storeId ??
                  context.tr('ca.noStoreChosen'),
            ),
          if (_lastCheck != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _CheckResultBanner(result: _lastCheck!),
          ],
          if (_form.usesWebhook && connected) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _WebhookPanel(provider: _provider, form: _form),
          ],
          const SizedBox(height: EcomsbdSpacing.md),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              FilledButton(
                onPressed: _busy ? null : () => _connect(account),
                style: _primaryButton,
                child: Text(switch (status) {
                  CourierAccountStatus.connected => context.tr('ca.replaceKeys'),
                  CourierAccountStatus.needsReconnect => context.tr(
                    'ca.reconnect',
                  ),
                  _ => context.tr('common.connect'),
                }),
              ),
              if (_form.requiresStore && connected)
                OutlinedButton(
                  onPressed: _busy ? null : _chooseStore,
                  child: Text(
                    account?.storeId == null
                        ? context.tr('ca.chooseStore')
                        : context.tr('ca.changeStore'),
                  ),
                ),
              if (connected)
                OutlinedButton(
                  onPressed: _busy ? null : _test,
                  child: Text(
                    _busy
                        ? context.tr('settings.checking')
                        : context.tr('ca.testConnection'),
                  ),
                ),
              if (account != null && status != CourierAccountStatus.disconnected)
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

String _providerSubtitle(CourierAccountStatus status, String name) =>
    switch (status) {
      CourierAccountStatus.connected => _t('ca.connectedSub'),
      CourierAccountStatus.needsReconnect => AppStrings(
        activeAppLocale,
      ).t('ca.needsReconnectProviderSub', <String, Object?>{'provider': name}),
      CourierAccountStatus.disconnected => AppStrings(
        activeAppLocale,
      ).t('ca.notConnectedProviderSub', <String, Object?>{'provider': name}),
      CourierAccountStatus.unknown => _t('ca.unreadable'),
    };

/// The callback URL, and an honest statement of what happens without it.
class _WebhookPanel extends ConsumerWidget {
  const _WebhookPanel({required this.provider, required this.form});

  final String provider;
  final ProviderConnectForm form;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final setup = ref.watch(webhookSetupProvider(provider));

    return setup.when(
      loading: () => const SizedBox.shrink(),
      error: (_, __) => const SizedBox.shrink(),
      data: (value) {
        if (!value.supported) {
          return const SizedBox.shrink();
        }
        return Container(
          width: double.infinity,
          padding: const EdgeInsets.all(EcomsbdSpacing.sm),
          decoration: BoxDecoration(
            color: value.secretConfigured
                ? EcomsbdColors.green.withValues(alpha: 0.08)
                : EcomsbdColors.amber.withValues(alpha: 0.10),
            borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Icon(
                    value.secretConfigured
                        ? Icons.check_circle_outline
                        : Icons.warning_amber_outlined,
                    size: 15,
                    color: value.secretConfigured
                        ? EcomsbdColors.green
                        : EcomsbdColors.amber,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      value.secretConfigured
                          ? context.tr('ca.webhookOn')
                          : context.tr('ca.webhookMissing'),
                      style: EcomsbdType.bodyStrong,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                // The courier's own reason, from the server, so a seller reads
                // why this matters for *this* courier rather than a generic note.
                value.help ?? form.webhookHelp ?? '',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              if (value.callbackUrl != null) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.xs),
                Text(
                  context.tr('ca.callbackUrl'),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted2,
                  ),
                ),
                const SizedBox(height: 2),
                SelectableText(
                  value.callbackUrl!,
                  style: EcomsbdType.caption.copyWith(fontFamily: 'monospace'),
                ),
                const SizedBox(height: EcomsbdSpacing.xs),
                OutlinedButton.icon(
                  onPressed: () async {
                    await Clipboard.setData(
                      ClipboardData(text: value.callbackUrl!),
                    );
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(content: Text(context.tr('ca.urlCopied'))),
                      );
                    }
                  },
                  icon: const Icon(Icons.copy_outlined, size: 15),
                  label: Text(context.tr('ca.copyUrl')),
                ),
              ],
            ],
          ),
        );
      },
    );
  }
}

/// Pick the pickup store bookings are made from.
class _StorePickerSheet extends ConsumerWidget {
  const _StorePickerSheet({required this.provider, required this.displayName});

  final String provider;
  final String displayName;

  static Future<CourierStore?> show(
    BuildContext context, {
    required String provider,
    required String displayName,
  }) {
    return showModalBottomSheet<CourierStore>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) =>
          _StorePickerSheet(provider: provider, displayName: displayName),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final stores = ref.watch(providerStoresProvider(provider));

    return Container(
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
          Text(context.tr('ca.choosePickupStore'), style: EcomsbdType.sectionTitle),
          const SizedBox(height: 3),
          Text(
            context.tr('ca.pickupStoreNote', <String, Object?>{
              'provider': displayName,
            }),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          stores.when(
            loading: () => SkeletonLoader.card(height: 90),
            error: (error, _) => error is ApiError
                ? ErrorStateCard(
                    error: error,
                    onRetry: () =>
                        ref.invalidate(providerStoresProvider(provider)),
                  )
                : EmptyState(
                    icon: Icons.error_outline,
                    title: context.tr('common.couldNotLoad'),
                    message: '$error',
                  ),
            data: (rows) => rows.isEmpty
                ? EmptyState(
                    icon: Icons.store_outlined,
                    title: context.tr('ca.noStores'),
                    message: context.tr('ca.noStoresNote', <String, Object?>{
                      'provider': displayName,
                    }),
                  )
                // Wrapped in a transparent Material: this sheet paints its
                // own background, and a ListTile needs a Material ancestor to
                // paint its ink splashes onto.
                : Material(
                    type: MaterialType.transparency,
                    child: Column(
                    children: <Widget>[
                      for (final store in rows)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(store.name, style: EcomsbdType.body),
                          subtitle: store.address == null
                              ? null
                              : Text(
                                  store.address!,
                                  style: EcomsbdType.caption.copyWith(
                                    color: EcomsbdColors.muted,
                                  ),
                                ),
                          trailing: const Icon(
                            Icons.chevron_right,
                            size: 18,
                            color: EcomsbdColors.muted2,
                          ),
                          onTap: () => Navigator.of(context).pop(store),
                        ),
                    ],
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}

/// The one form in the app that carries a secret.
///
/// The fields are **described by the server**, not hard-coded here. Steadfast
/// asks for an API key and a secret key, Pathao for a Client ID and a Client
/// Secret, and the next courier will ask for something else again. Rendering
/// from the declaration means the labels a seller reads come from the same
/// place the server's validation errors do, so the two cannot disagree — and a
/// new courier does not need an app release to become connectable.
///
/// When no form is supplied, the sheet falls back to the two-field layout V1
/// shipped, so the Steadfast path is unchanged.
class ConnectCourierSheet extends ConsumerStatefulWidget {
  const ConnectCourierSheet({
    required this.provider,
    super.key,
    this.isReconnect = false,
    this.form,
    this.sandbox = false,
  });

  final String provider;
  final bool isReconnect;

  /// The server's description of this courier's connect form.
  final ProviderConnectForm? form;

  /// Whether the account is already on the courier's sandbox.
  final bool sandbox;

  static Future<ConnectionTestResult?> show(
    BuildContext context, {
    required String provider,
    bool isReconnect = false,
    ProviderConnectForm? form,
    bool sandbox = false,
  }) {
    return showModalBottomSheet<ConnectionTestResult>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => ConnectCourierSheet(
        provider: provider,
        isReconnect: isReconnect,
        form: form,
        sandbox: sandbox,
      ),
    );
  }

  @override
  ConsumerState<ConnectCourierSheet> createState() =>
      _ConnectCourierSheetState();
}

class _ConnectCourierSheetState extends ConsumerState<ConnectCourierSheet> {
  final Map<String, TextEditingController> _fields =
      <String, TextEditingController>{};
  final TextEditingController _webhookSecret = TextEditingController();
  bool _busy = false;
  bool _sandbox = false;
  ApiError? _error;

  /// The fields to render. Falls back to the V1 Steadfast pair when the server
  /// has not described this courier.
  List<ConnectFormField> get _formFields =>
      widget.form?.fields ?? _fallbackFields;

  /// The V1 Steadfast pair, used when the server has not described this
  /// courier. Both halves are secret — both are encrypted at rest and neither
  /// is ever returned — but only the *secret* half is masked on screen: the key
  /// identifies which credential is loaded and stays readable, which is what
  /// lets a seller check they pasted the right one.
  static const List<ConnectFormField> _fallbackFields = <ConnectFormField>[
    ConnectFormField(
      name: 'api_key',
      labelEn: 'API Key',
      labelBn: 'API Key',
      secret: true,
      required: true,
      inputType: 'text',
    ),
    ConnectFormField(
      name: 'secret_key',
      labelEn: 'Secret Key',
      labelBn: 'Secret Key',
      secret: true,
      required: true,
    ),
  ];

  @override
  void initState() {
    super.initState();
    _sandbox = widget.sandbox;
    for (final field in _formFields) {
      _fields[field.name] = TextEditingController();
    }
  }

  @override
  void dispose() {
    for (final controller in _fields.values) {
      controller.dispose();
    }
    _webhookSecret.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ref.read(courierRepositoryProvider).connect(
        provider: widget.provider,
        credentials: <String, String>{
          for (final entry in _fields.entries)
            entry.key: entry.value.text.trim(),
        },
        config: <String, dynamic>{
          if (widget.form?.supportsSandbox ?? false) 'sandbox': _sandbox,
        },
        webhookSecret: _webhookSecret.text.trim(),
      );
      // Cleared immediately: the values have left the device and there is no
      // reason for them to stay in a controller behind a dismissed sheet.
      for (final controller in _fields.values) {
        controller.clear();
      }
      _webhookSecret.clear();
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
    final form = widget.form;
    final name = form?.displayName ?? 'Steadfast';
    final canSubmit = _formFields.every(
      (field) =>
          !field.required || (_fields[field.name]?.text.trim().isNotEmpty ?? false),
    );

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
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              const _SheetGrip(),
              const SizedBox(height: EcomsbdSpacing.md),
              Text(
                form == null
                    ? (widget.isReconnect
                          ? context.tr('ca.reconnectSteadfast')
                          : context.tr('ca.connectSteadfast'))
                    : context.tr(
                        widget.isReconnect
                            ? 'ca.reconnectProvider'
                            : 'ca.connectProvider',
                        <String, Object?>{'provider': name},
                      ),
                style: EcomsbdType.sectionTitle,
              ),
              const SizedBox(height: 3),
              Text(
                form == null
                    ? context.tr('ca.keysNote')
                    : context.tr('ca.keysNoteProvider', <String, Object?>{
                        'provider': name,
                      }),
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              for (final field in _formFields) ...<Widget>[
                LabelledField(
                  label: field.label,
                  controller: _fields[field.name]!,
                  // The courier's own words for where to find this value.
                  hint: field.help,
                  obscureText: field.obscure,
                  onChanged: (_) => setState(() {}),
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
              ],
              if (form?.supportsSandbox ?? false)
                // A Row rather than a SwitchListTile: this sheet paints its own
                // background, and a ListTile inside a decorated box hides its
                // own ink splashes.
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(
                            context.tr('ca.useSandbox'),
                            style: EcomsbdType.body,
                          ),
                          Text(
                            context.tr('ca.useSandboxNote', <String, Object?>{
                              'provider': name,
                            }),
                            style: EcomsbdType.caption.copyWith(
                              color: EcomsbdColors.muted,
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: EcomsbdSpacing.xs),
                    Switch.adaptive(
                      value: _sandbox,
                      onChanged: _busy
                          ? null
                          : (value) => setState(() => _sandbox = value),
                    ),
                  ],
                ),
              if (form?.usesWebhook ?? false) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.xs),
                LabelledField(
                  label: context.tr('ca.webhookSecretField'),
                  controller: _webhookSecret,
                  hint: context.tr('ca.webhookSecretHint'),
                  obscureText: true,
                  onChanged: (_) => setState(() {}),
                ),
                if ((form?.webhookHelp ?? '').isNotEmpty) ...<Widget>[
                  const SizedBox(height: 3),
                  Text(
                    form!.webhookHelp!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                  ),
                ],
                const SizedBox(height: EcomsbdSpacing.sm),
              ],
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
                      ? (form == null
                            ? context.tr('ca.checkingWith')
                            : context.tr('ca.checkingWithProvider',
                                <String, Object?>{'provider': name}))
                      : context.tr('ca.saveAndCheck'),
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.xs),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Icon(
                    Icons.lock_outline,
                    size: 14,
                    color: EcomsbdColors.muted2,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      form == null
                          ? context.tr('ca.readOnlyNote')
                          : context.tr('ca.readOnlyNoteProvider',
                              <String, Object?>{'provider': name}),
                      style: EcomsbdType.caption,
                    ),
                  ),
                ],
              ),
            ],
          ),
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
