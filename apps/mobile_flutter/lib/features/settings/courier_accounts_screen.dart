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

/// Courier accounts: the one place a seller sees and manages every courier.
///
/// Brief sections 4 and 5. The list shows every courier ecomsbd knows about —
/// connected or not, switched on for this shop or not, integrated or still
/// waiting on an official contract — and each connectable one leads to a
/// single management screen. Nothing here is decided by a courier's name: the
/// connect form, the capabilities and the on/off state all come from the
/// server, so a courier the server does not offer cannot be offered here.
///
/// Three things these screens refuse to do:
///
/// * **Show a secret after it is saved.** There is no field for it, because
///   there is no response that carries one. The masked identifier is all a
///   seller gets back, and it is all they need to recognise which key is in.
/// * **Say "your key is wrong" when it does not know.** A courier outage and a
///   rejected credential look identical if you only have a boolean, so the four
///   validation outcomes are rendered as four different things.
/// * **Offer a courier that cannot work.** One without a verified contract is
///   listed as unavailable, with no Connect button, rather than hidden or
///   dressed up as connectable.
class CourierAccountsScreen extends ConsumerWidget {
  const CourierAccountsScreen({super.key});

  static const String steadfast = 'steadfast';

  /// Manual mode. The server lists it beside the couriers, but it has nothing
  /// to connect; it has its own card at the foot of the screen instead.
  static const String manual = 'manual';

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final viewOnly = isCourierRoleRefusal(
      ref.watch(courierAccountsProvider).error,
    );

    return DetailScaffold(
      title: context.tr('ca.title'),
      children: <Widget>[
        Text(
          context.tr('ca.intro'),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        if (viewOnly) ...<Widget>[
          const _OwnerOnlyNotice(),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        _CourierList(viewOnly: viewOnly),
        const _ManualModeAlwaysWorks(),
      ],
    );
  }
}

/// True when the server refused because of the person's role.
///
/// The accounts list is owner-only (`courier.credential_manage`), so a refusal
/// there is how this screen learns it is being read by someone who may look
/// but not change anything. The role matrix stays on the server; the app hides
/// what the server would refuse, and the server refuses it regardless.
bool isCourierRoleRefusal(Object? error) =>
    error is ApiError && error.code == ApiErrorCode.forbidden;

/// Where one courier stands for this shop.
enum CourierConnection {
  connected,
  needsReconnect,
  notConnected,

  /// Integrated, but not switched on for this shop.
  disabled,

  /// No verified contract, so nothing to connect.
  unavailable,
  unknown;

  String label(BuildContext context) => switch (this) {
    CourierConnection.connected => context.tr('provider.connected'),
    CourierConnection.needsReconnect => context.tr('provider.needsReconnect'),
    CourierConnection.notConnected => context.tr('provider.notConnected'),
    CourierConnection.disabled => context.tr('ca.stateDisabled'),
    CourierConnection.unavailable => context.tr('ca.stateUnavailable'),
    CourierConnection.unknown => context.tr('provider.unknown'),
  };

  Tone get tone => switch (this) {
    CourierConnection.connected => Tone.good,
    CourierConnection.needsReconnect => Tone.warning,
    _ => Tone.neutral,
  };
}

/// Work out a courier's state from what the server said.
///
/// Shared with the Connections & Integrations hub, so the two screens can
/// never disagree about a courier. "Disabled" comes only from the server's
/// own switch for this shop; a courier that is switched on and has nothing
/// saved yet is "not connected", with a Connect button.
///
/// Someone who may manage credentials has the account itself. Anyone else has
/// only the booking-availability list, which says the same thing in booking
/// terms — and is itself withheld from roles that cannot book, in which case
/// the honest answer is "unknown".
CourierConnection courierConnectionFor(
  CourierProviderInfo info, {
  CourierAccount? account,
  BookableCourier? bookable,
  bool viewOnly = false,
}) {
  if (info.connectForm == null) {
    return CourierConnection.unavailable;
  }
  if (!info.enabled) {
    return CourierConnection.disabled;
  }
  if (viewOnly) {
    if (bookable == null) {
      return CourierConnection.unknown;
    }
    if (bookable.bookable) {
      return CourierConnection.connected;
    }
    return switch (bookable.block) {
      BookableBlock.needsPickupStore ||
      BookableBlock.providerUnavailable => CourierConnection.connected,
      BookableBlock.needsReconnect => CourierConnection.needsReconnect,
      BookableBlock.notConnected => CourierConnection.notConnected,
      BookableBlock.notEnabled => CourierConnection.disabled,
      _ => CourierConnection.unknown,
    };
  }
  return switch (account?.status) {
    CourierAccountStatus.connected => CourierConnection.connected,
    CourierAccountStatus.needsReconnect => CourierConnection.needsReconnect,
    CourierAccountStatus.unknown => CourierConnection.unknown,
    _ => CourierConnection.notConnected,
  };
}

/// Whether a connected courier still cannot book because its mandatory
/// pickup store has not been chosen. "Connected" alone would hide that.
bool courierNeedsPickupStore(
  CourierProviderInfo info,
  CourierConnection connection, {
  CourierAccount? account,
  BookableCourier? bookable,
  bool viewOnly = false,
}) => viewOnly
    ? bookable?.block == BookableBlock.needsPickupStore
    : (info.connectForm?.requiresStore ?? false) &&
          connection == CourierConnection.connected &&
          account?.storeId == null;

/// This shop's account for [provider] in [rows], if it has one.
CourierAccount? courierAccountOf(List<CourierAccount>? rows, String provider) {
  for (final row in rows ?? const <CourierAccount>[]) {
    if (row.provider == provider) {
      return row;
    }
  }
  return null;
}

/// Whether [provider] can book, from the booking-availability list [rows].
BookableCourier? bookableCourierOf(
  List<BookableCourier>? rows,
  String provider,
) {
  for (final row in rows ?? const <BookableCourier>[]) {
    if (row.provider == provider) {
      return row;
    }
  }
  return null;
}

/// Whether stored credentials exist for this account.
///
/// A disconnected account keeps its row (bookings reference it) and even its
/// old masked identifier, but its credentials are erased, so nothing about
/// them is shown.
bool _hasCredentials(CourierAccount? account) =>
    account != null && account.status != CourierAccountStatus.disconnected;

/// The saved identifier as a seller sees it: `••••••••AB12`.
///
/// The server sends `****AB12` — the last four characters of the key and
/// nothing else — so this only restyles what is already safe to show.
String maskedForDisplay(String masked) {
  final visible = masked.replaceFirst(RegExp(r'^\*+'), '');
  return '••••••••$visible';
}

/// Every courier ecomsbd knows about, as one row each.
class _CourierList extends ConsumerWidget {
  const _CourierList({required this.viewOnly});

  final bool viewOnly;

  Widget _loadError(
    BuildContext context,
    WidgetRef ref,
    Object error,
    ProviderOrFamily provider,
  ) {
    return error is ApiError
        ? ErrorStateCard(error: error, onRetry: () => ref.invalidate(provider))
        : EmptyState(
            icon: Icons.error_outline,
            title: context.tr('common.couldNotLoad'),
            message: context.tr('common.somethingWentWrong'),
          );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final providers = ref.watch(courierProvidersProvider);
    final accounts = ref.watch(courierAccountsProvider);
    final bookable = viewOnly
        ? ref.watch(bookableCouriersProvider).valueOrNull
        : null;

    return providers.when(
      loading: () => SkeletonLoader.card(height: 180),
      error: (error, _) =>
          _loadError(context, ref, error, courierProvidersProvider),
      data: (rows) {
        if (!viewOnly && accounts.hasError) {
          return _loadError(
            context,
            ref,
            accounts.error!,
            courierAccountsProvider,
          );
        }
        if (!viewOnly && !accounts.hasValue) {
          return SkeletonLoader.card(height: 180);
        }
        final couriers = <CourierProviderInfo>[
          for (final row in rows)
            if (row.provider != CourierAccountsScreen.manual) row,
        ];
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            for (final info in couriers) ...<Widget>[
              _CourierRow(
                info: info,
                account: courierAccountOf(accounts.valueOrNull, info.provider),
                bookable: bookableCourierOf(bookable, info.provider),
                viewOnly: viewOnly,
              ),
              const SizedBox(height: EcomsbdSpacing.md),
            ],
          ],
        );
      },
    );
  }
}

/// One courier in the list: its state, how it is identified, what it can do,
/// and the one action that moves it forward.
class _CourierRow extends ConsumerWidget {
  const _CourierRow({
    required this.info,
    required this.account,
    required this.bookable,
    required this.viewOnly,
  });

  final CourierProviderInfo info;
  final CourierAccount? account;
  final BookableCourier? bookable;
  final bool viewOnly;

  void _manage(BuildContext context) {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => CourierAccountManageScreen(info: info),
      ),
    );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final connection = courierConnectionFor(
      info,
      account: account,
      bookable: bookable,
      viewOnly: viewOnly,
    );
    final form = info.connectForm;
    final hasCredentials =
        !viewOnly && form != null && _hasCredentials(account);
    final canConnect =
        !viewOnly && connection == CourierConnection.notConnected;
    final needsStore = courierNeedsPickupStore(
      info,
      connection,
      account: account,
      bookable: bookable,
      viewOnly: viewOnly,
    );

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const _CourierMark(),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Text(info.displayName, style: EcomsbdType.sectionTitle),
              ),
              StatusChip(
                label: connection.label(context),
                tone: connection.tone,
              ),
            ],
          ),
          // A sandbox account books nothing real, so it is never allowed to
          // pass for a live one at a glance.
          if (hasCredentials && (account?.sandbox ?? false))
            const _SandboxMarker(),
          const SizedBox(height: EcomsbdSpacing.xs),
          _StatusLine(
            connection: connection,
            info: info,
            needsStore: needsStore,
            unavailableAtCourier:
                viewOnly &&
                bookable?.block == BookableBlock.providerUnavailable,
          ),
          if (hasCredentials && account?.maskedIdentifier != null)
            _DetailRow(
              label: _primaryLabel(context, form),
              value: maskedForDisplay(account!.maskedIdentifier!),
            ),
          if (hasCredentials && account?.lastVerifiedAt != null)
            _DetailRow(
              label: context.tr('ca.lastChecked'),
              value: formatRelative(account!.lastVerifiedAt),
            ),
          if (connection != CourierConnection.unavailable)
            _CapabilityChips(capabilities: info.capabilities),
          if (hasCredentials || canConnect) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.md),
            if (hasCredentials)
              OutlinedButton(
                onPressed: () => _manage(context),
                style: _secondaryButton,
                child: Text(context.tr('ca.manage')),
              )
            else
              FilledButton(
                onPressed: () => connectCourier(context, ref, info),
                style: _primaryButton,
                child: Text(context.tr('common.connect')),
              ),
          ],
        ],
      ),
    );
  }
}

/// The label of the field the masked identifier was taken from.
String _primaryLabel(BuildContext context, ProviderConnectForm? form) =>
    form == null || form.fields.isEmpty
    ? context.tr('ca.apiKeyLabel')
    : form.fields.first.label;

/// Stand-in for a courier logo. The app ships no courier artwork, and a
/// borrowed brand logo is not ours to use.
class _CourierMark extends StatelessWidget {
  const _CourierMark();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 34,
      height: 34,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: EcomsbdColors.orangeSoft,
        borderRadius: BorderRadius.circular(12),
      ),
      child: const Icon(
        Icons.local_shipping_outlined,
        size: 18,
        color: EcomsbdColors.orange,
      ),
    );
  }
}

/// The Sandbox chip, on a line of its own under the courier's name so the
/// header row never has to fit two chips beside a long name on a small phone.
class _SandboxMarker extends StatelessWidget {
  const _SandboxMarker();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.xs),
      child: StatusChip(
        label: context.tr('ca.sandboxChip'),
        tone: Tone.warning,
      ),
    );
  }
}

/// One sentence on what a courier's state means for the seller.
class _StatusLine extends StatelessWidget {
  const _StatusLine({
    required this.connection,
    required this.info,
    this.needsStore = false,
    this.unavailableAtCourier = false,
  });

  final CourierConnection connection;
  final CourierProviderInfo info;
  final bool needsStore;
  final bool unavailableAtCourier;

  @override
  Widget build(BuildContext context) {
    final name = <String, Object?>{'provider': info.displayName};
    final muted = EcomsbdType.caption.copyWith(color: EcomsbdColors.muted);

    if (connection == CourierConnection.unavailable) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('ca.officialRequired'), style: EcomsbdType.label),
          const SizedBox(height: 2),
          Text(context.tr('ca.unavailableSub', name), style: muted),
        ],
      );
    }

    // "Connected" is not the whole truth when a mandatory pickup store is
    // missing, so this says what is actually blocking a booking.
    if (needsStore) {
      return Text(
        context.tr('ca.needsStoreSub'),
        style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
      );
    }

    final text = switch (connection) {
      CourierConnection.connected =>
        unavailableAtCourier
            ? context.tr('bookable.unavailable')
            : info.supports('create_single')
            ? context.tr('ca.readyToBook')
            : null,
      CourierConnection.needsReconnect => context.tr(
        'ca.needsReconnectProviderSub',
        name,
      ),
      CourierConnection.notConnected => context.tr(
        'ca.notConnectedProviderSub',
        name,
      ),
      CourierConnection.disabled => context.tr('ca.disabledSub', name),
      CourierConnection.unknown => context.tr('ca.unreadable'),
      CourierConnection.unavailable => null,
    };
    return text == null ? const SizedBox.shrink() : Text(text, style: muted);
  }
}

/// What a courier is verified to do, from the server's manifest.
///
/// Only capabilities the documentation positively confirms are shown. An
/// "unknown" is not a feature, so it is not drawn as one.
class _CapabilityChips extends StatelessWidget {
  const _CapabilityChips({required this.capabilities});

  final Map<String, String> capabilities;

  @override
  Widget build(BuildContext context) {
    final shown = <(String, String)>[
      for (final entry in _shownCapabilities)
        if (capabilities[entry.$1] == 'true') entry,
    ];
    if (shown.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.sm),
      child: Wrap(
        spacing: EcomsbdSpacing.xs,
        runSpacing: EcomsbdSpacing.xs,
        children: <Widget>[
          for (final entry in shown)
            StatusChip(
              label: context.tr(entry.$2),
              tone: Tone.neutral,
              showIcon: false,
            ),
        ],
      ),
    );
  }
}

class _OwnerOnlyNotice extends StatelessWidget {
  const _OwnerOnlyNotice();

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Icon(Icons.lock_outline, size: 18, color: EcomsbdColors.muted),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(context.tr('ca.ownerOnly'), style: EcomsbdType.caption),
          ),
        ],
      ),
    );
  }
}

/// Connect [info]'s courier: the server-described form, then its account.
///
/// The one way in, used by this list and by the Connections & Integrations
/// hub, so both save, check and land on the account the same way.
Future<void> connectCourier(
  BuildContext context,
  WidgetRef ref,
  CourierProviderInfo info,
) async {
  final result = await ConnectCourierSheet.show(
    context,
    provider: info.provider,
    form: info.connectForm,
  );
  if (result == null || !context.mounted) {
    return;
  }
  _invalidateCourierState(ref, info.provider);
  // Straight on to the account, where the outcome of the check is shown and
  // anything still missing — a pickup store, the callback URL — is set up.
  await Navigator.of(context).push(
    MaterialPageRoute<void>(
      builder: (_) =>
          CourierAccountManageScreen(info: info, initialCheck: result),
    ),
  );
}

void _invalidateCourierState(WidgetRef ref, String provider) {
  ref.invalidate(courierAccountsProvider);
  ref.invalidate(bookableCouriersProvider);
  ref.invalidate(webhookSetupProvider(provider));
}

/// One courier account: its credentials (masked), its checks, and every
/// change a seller can make to it.
///
/// Reached from the accounts list. Only someone who may manage credentials
/// gets here, and the server re-checks that on every call regardless.
class CourierAccountManageScreen extends ConsumerStatefulWidget {
  const CourierAccountManageScreen({
    required this.info,
    super.key,
    this.initialCheck,
  });

  final CourierProviderInfo info;

  /// The outcome of the connect that led here, shown until the next check.
  final ConnectionTestResult? initialCheck;

  @override
  ConsumerState<CourierAccountManageScreen> createState() =>
      _CourierAccountManageScreenState();
}

class _CourierAccountManageScreenState
    extends ConsumerState<CourierAccountManageScreen> {
  bool _busy = false;
  late ConnectionTestResult? _lastCheck = widget.initialCheck;

  String get _provider => widget.info.provider;
  String get _name => widget.info.displayName;
  ProviderConnectForm? get _form => widget.info.connectForm;

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

  /// Enter credentials: first ones, replacements, or the same ones again after
  /// the courier stopped accepting them. The form always starts empty — there
  /// is nothing saved that could be put back into it.
  Future<void> _enterCredentials(CourierAccount? account) async {
    final status = account?.status;
    final result = await ConnectCourierSheet.show(
      context,
      provider: _provider,
      form: _form,
      isReconnect: status == CourierAccountStatus.needsReconnect,
      isUpdate: status == CourierAccountStatus.connected,
      sandbox: account?.sandbox ?? false,
    );
    if (result != null && mounted) {
      _invalidateCourierState(ref, _provider);
      setState(() => _lastCheck = result);
    }
  }

  Future<void> _test() async {
    setState(() => _lastCheck = null);
    await _guard(() async {
      final result = await ref
          .read(courierRepositoryProvider)
          .testConnection(_provider);
      _invalidateCourierState(ref, _provider);
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
            'provider': _name,
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
    if (confirmed != true || !mounted) {
      return;
    }
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    final done = context.tr('ca.disconnected', <String, Object?>{
      'provider': _name,
    });
    var disconnected = false;
    await _guard(() async {
      await ref.read(courierRepositoryProvider).disconnect(_provider);
      _invalidateCourierState(ref, _provider);
      disconnected = true;
    });
    if (disconnected && mounted) {
      // Back to the list, which now shows this courier as not connected.
      messenger.showSnackBar(SnackBar(content: Text(done)));
      navigator.pop();
    }
  }

  Future<void> _chooseStore() async {
    final chosen = await _StorePickerSheet.show(
      context,
      provider: _provider,
      displayName: _name,
      optional: !(_form?.requiresStore ?? false),
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
      ref.invalidate(bookableCouriersProvider);
    });
  }

  @override
  Widget build(BuildContext context) {
    final account = ref.watch(courierAccountProvider(_provider)).valueOrNull;
    final evidence = ref.watch(providerEvidenceProvider(_provider));
    final form = _form;
    final connection = courierConnectionFor(widget.info, account: account);
    final status = account?.status ?? CourierAccountStatus.disconnected;
    final hasCredentials = _hasCredentials(account);
    final connected = status == CourierAccountStatus.connected;
    final requiresStore = form?.requiresStore ?? false;
    // A courier where a store may be chosen but need not be (RedX) gets the
    // same row and button, labelled as optional.
    final supportsStore = requiresStore || (form?.supportsStore ?? false);
    final needsStore = requiresStore && connected && account?.storeId == null;

    // Credentials are not entered for a courier the shop may not use. Ones
    // saved before it was switched off can still be tested and removed.
    final String? primaryAction = !widget.info.enabled
        ? null
        : switch (status) {
            CourierAccountStatus.connected => context.tr(
              'ca.updateCredentials',
            ),
            CourierAccountStatus.needsReconnect => context.tr('ca.reconnect'),
            CourierAccountStatus.unknown => context.tr('ca.updateCredentials'),
            CourierAccountStatus.disconnected => context.tr('common.connect'),
          };

    return DetailScaffold(
      title: _name,
      subtitle: context.tr('ca.title'),
      children: <Widget>[
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  const _CourierMark(),
                  const SizedBox(width: EcomsbdSpacing.sm),
                  Expanded(child: Text(_name, style: EcomsbdType.sectionTitle)),
                  StatusChip(
                    label: connection.label(context),
                    tone: connection.tone,
                  ),
                ],
              ),
              if (account?.sandbox ?? false) const _SandboxMarker(),
              const SizedBox(height: EcomsbdSpacing.xs),
              _StatusLine(
                connection: connection,
                info: widget.info,
                needsStore: needsStore,
              ),
              if (hasCredentials && account?.lastVerifiedAt != null)
                _DetailRow(
                  label: context.tr('ca.lastChecked'),
                  value: formatRelative(account!.lastVerifiedAt),
                ),
              if (hasCredentials && account?.reportedBalancePaisa != null)
                _DetailRow(
                  // Labelled as the courier's own number, never merged with
                  // the COD outstanding total on the Money screen: they
                  // measure different things (brief section 19).
                  label: context.tr(
                    'ca.providerReportedBalance',
                    <String, Object?>{'provider': _name},
                  ),
                  value: Money(account!.reportedBalancePaisa!).format(),
                ),
              if (supportsStore && connected)
                _DetailRow(
                  label: requiresStore
                      ? context.tr('ca.pickupStore')
                      : context.tr('ca.pickupStoreOptional'),
                  value:
                      account?.storeName ??
                      account?.storeId ??
                      context.tr('ca.noStoreChosen'),
                ),
            ],
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        if (form != null) ...<Widget>[
          _CredentialsCard(
            form: form,
            account: account,
            hasCredentials: hasCredentials,
            providerName: _name,
          ),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        if (_lastCheck != null) ...<Widget>[
          _CheckResultBanner(result: _lastCheck!, providerName: _name),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        if ((form?.usesWebhook ?? false) && connected) ...<Widget>[
          _WebhookPanel(provider: _provider, form: form!),
          const SizedBox(height: EcomsbdSpacing.md),
        ],
        if (primaryAction != null) ...<Widget>[
          FilledButton(
            onPressed: _busy ? null : () => _enterCredentials(account),
            style: _primaryButton,
            child: Text(primaryAction),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
        ],
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            if (hasCredentials)
              OutlinedButton(
                onPressed: _busy ? null : _test,
                child: Text(
                  _busy
                      ? context.tr('settings.checking')
                      : context.tr('ca.testConnection'),
                ),
              ),
            if (supportsStore && connected)
              OutlinedButton(
                onPressed: _busy ? null : _chooseStore,
                child: Text(
                  account?.storeId == null
                      ? context.tr('ca.chooseStore')
                      : context.tr('ca.changeStore'),
                ),
              ),
            if (hasCredentials)
              TextButton(
                onPressed: _busy ? null : _disconnect,
                style: TextButton.styleFrom(foregroundColor: EcomsbdColors.red),
                child: Text(context.tr('common.disconnect')),
              ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        evidence.when(
          loading: () => const SizedBox.shrink(),
          error: (_, __) => const SizedBox.shrink(),
          data: (value) =>
              _WhatThisCourierSupports(evidence: value, providerName: _name),
        ),
      ],
    );
  }
}

/// The saved credentials, as far as they can be shown: which key is loaded,
/// and that the secret is in place. Never the secret itself.
class _CredentialsCard extends StatelessWidget {
  const _CredentialsCard({
    required this.form,
    required this.account,
    required this.hasCredentials,
    required this.providerName,
  });

  final ProviderConnectForm form;
  final CourierAccount? account;
  final bool hasCredentials;
  final String providerName;

  @override
  Widget build(BuildContext context) {
    final masked = account?.maskedIdentifier;

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('ca.credentialsTitle'), style: EcomsbdType.label),
          for (final (index, field) in form.fields.indexed)
            _DetailRow(
              label: field.label,
              value: !hasCredentials
                  ? context.tr('ca.notSet')
                  // The first field is the one the server masks for display.
                  : index == 0 && masked != null
                  ? maskedForDisplay(masked)
                  : context.tr('ca.configured'),
            ),
          const SizedBox(height: EcomsbdSpacing.sm),
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
                  context.tr('ca.credentialsNote', <String, Object?>{
                    'provider': providerName,
                  }),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// The four validation outcomes, rendered as four different things.
///
/// This is the whole point of the enum: "we could not check" must not look
/// like "your key is wrong". A seller who re-types a working key because the
/// courier had a bad minute has been failed by this widget.
class _CheckResultBanner extends StatelessWidget {
  const _CheckResultBanner({required this.result, required this.providerName});

  final ConnectionTestResult result;
  final String providerName;

  @override
  Widget build(BuildContext context) {
    final (tone, title) = switch (result.result) {
      CredentialCheck.valid => (Tone.good, context.tr('ca.connectionOk')),
      CredentialCheck.invalid => (Tone.bad, context.tr('ca.connectionFailed')),
      CredentialCheck.providerUnavailable => (
        Tone.warning,
        context.tr('ca.noAnswerProvider', <String, Object?>{
          'provider': providerName,
        }),
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
          if (result.message.isNotEmpty) ...<Widget>[
            const SizedBox(height: 2),
            Text(result.message, style: EcomsbdType.caption),
          ],
        ],
      ),
    );
  }
}

/// What the courier's own documentation supports — and what it does not say.
class _WhatThisCourierSupports extends StatelessWidget {
  const _WhatThisCourierSupports({
    required this.evidence,
    required this.providerName,
  });

  final ProviderEvidence evidence;
  final String providerName;

  @override
  Widget build(BuildContext context) {
    final name = <String, Object?>{'provider': providerName};

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr('ca.supportsProvider', name),
            style: EcomsbdType.label,
          ),
          const SizedBox(height: 3),
          Text(
            context.tr('ca.supportsSource', <String, Object?>{
              ...name,
              'version': evidence.documentationVersion ?? '',
            }),
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
                const Icon(
                  Icons.sync_rounded,
                  size: 15,
                  color: EcomsbdColors.muted2,
                ),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    context.tr('ca.pollingNoteProvider', name),
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
  ('webhook', 'ca.capWebhook'),
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
        if (form.webhookSecretGenerated) {
          return _GeneratedWebhookUrl(setup: value, form: form);
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

/// The callback URL for a courier whose callbacks authenticate by a token that
/// ecomsbd puts in the URL itself (RedX).
///
/// Worded as "here is the URL", never "status updates are set up": issuing
/// the URL says nothing about whether the seller has pasted it into the
/// courier's panel yet, and status is kept current by polling either way.
class _GeneratedWebhookUrl extends StatelessWidget {
  const _GeneratedWebhookUrl({required this.setup, required this.form});

  final WebhookSetup setup;
  final ProviderConnectForm form;

  @override
  Widget build(BuildContext context) {
    final url = setup.callbackUrl;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: EcomsbdColors.blueSoft,
        borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              const Icon(
                Icons.link_rounded,
                size: 15,
                color: EcomsbdColors.blue,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  context.tr('ca.webhookUrlReady', <String, Object?>{
                    'provider': form.displayName,
                  }),
                  style: EcomsbdType.bodyStrong,
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            setup.help ?? form.webhookHelp ?? '',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (url != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            SelectableText(
              url,
              style: EcomsbdType.caption.copyWith(fontFamily: 'monospace'),
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
            OutlinedButton.icon(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: url));
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
  }
}

/// Pick the pickup store bookings are made from.
class _StorePickerSheet extends ConsumerWidget {
  const _StorePickerSheet({
    required this.provider,
    required this.displayName,
    this.optional = false,
  });

  final String provider;
  final String displayName;

  /// Whether the courier books without a chosen store too (RedX).
  final bool optional;

  static Future<CourierStore?> show(
    BuildContext context, {
    required String provider,
    required String displayName,
    bool optional = false,
  }) {
    return showModalBottomSheet<CourierStore>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (_) => _StorePickerSheet(
        provider: provider,
        displayName: displayName,
        optional: optional,
      ),
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
          Text(
            context.tr('ca.choosePickupStore'),
            style: EcomsbdType.sectionTitle,
          ),
          const SizedBox(height: 3),
          Text(
            context.tr(
              optional ? 'ca.pickupStoreOptionalNote' : 'ca.pickupStoreNote',
              <String, Object?>{'provider': displayName},
            ),
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
                    message: context.tr('common.somethingWentWrong'),
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
/// It always opens empty. Updating credentials means typing replacements; the
/// saved ones are never read back to fill it, because nothing can read them.
///
/// When no form is supplied, the sheet falls back to the two-field layout V1
/// shipped, so the Steadfast path is unchanged.
class ConnectCourierSheet extends ConsumerStatefulWidget {
  const ConnectCourierSheet({
    required this.provider,
    super.key,
    this.isReconnect = false,
    this.isUpdate = false,
    this.form,
    this.sandbox = false,
  });

  final String provider;
  final bool isReconnect;

  /// Replacing credentials on an account that is connected and working.
  final bool isUpdate;

  /// The server's description of this courier's connect form.
  final ProviderConnectForm? form;

  /// Whether the account is already on the courier's sandbox.
  final bool sandbox;

  static Future<ConnectionTestResult?> show(
    BuildContext context, {
    required String provider,
    bool isReconnect = false,
    bool isUpdate = false,
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
        isUpdate: isUpdate,
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
      final result = await ref
          .read(courierRepositoryProvider)
          .connect(
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

  String _title(BuildContext context, String name) {
    if (widget.form == null && !widget.isUpdate) {
      return widget.isReconnect
          ? context.tr('ca.reconnectSteadfast')
          : context.tr('ca.connectSteadfast');
    }
    final key = widget.isReconnect
        ? 'ca.reconnectProvider'
        : widget.isUpdate
        ? 'ca.updateProvider'
        : 'ca.connectProvider';
    return context.tr(key, <String, Object?>{'provider': name});
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;
    final form = widget.form;
    final name = form?.displayName ?? 'Steadfast';
    final canSubmit = _formFields.every(
      (field) =>
          !field.required ||
          (_fields[field.name]?.text.trim().isNotEmpty ?? false),
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
              Text(_title(context, name), style: EcomsbdType.sectionTitle),
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
              // Only where the seller copies a secret out of the courier's
              // panel. A courier whose secret ecomsbd issues inside the
              // callback URL (RedX) has nothing to type here.
              if (form?.asksForWebhookSecret ?? false) ...<Widget>[
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
                            : context.tr(
                                'ca.checkingWithProvider',
                                <String, Object?>{'provider': name},
                              ))
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
                          : context.tr(
                              'ca.readOnlyNoteProvider',
                              <String, Object?>{'provider': name},
                            ),
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
          const SizedBox(width: EcomsbdSpacing.sm),
          Flexible(
            child: Text(
              value,
              style: EcomsbdType.label,
              textAlign: TextAlign.end,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
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

final ButtonStyle _primaryButton = FilledButton.styleFrom(
  backgroundColor: EcomsbdColors.orange,
  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
  shape: const StadiumBorder(),
  textStyle: EcomsbdType.label,
);

final ButtonStyle _secondaryButton = OutlinedButton.styleFrom(
  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
  shape: const StadiumBorder(),
  textStyle: EcomsbdType.label,
);
