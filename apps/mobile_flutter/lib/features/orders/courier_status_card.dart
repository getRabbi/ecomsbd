import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/inputs.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Read where no `BuildContext` exists, so the active locale is resolved
/// directly -- the same approach `formatRelative` and `order_status.dart` use.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// A parcel's courier state, on the order detail screen.
///
/// Brief section 34: show the courier, the tracking code, the courier's own
/// words, the normalized status, when it was last checked, and the timeline —
/// **without** drowning the seller in provider internals.
///
/// Two judgements are encoded here:
///
/// * The courier's raw status is shown **next to** the plain-language one, not
///   instead of it. A seller ringing Steadfast support needs to be able to say
///   the word Steadfast uses; a seller deciding whether to chase a parcel needs
///   a sentence in their own language.
/// * "Checked 12m ago" is labelled as a *check*, not as an event. Steadfast's
///   status response carries no timestamp, so presenting the observation time
///   as the moment the courier acted would invent a history.
class CourierStatusCard extends ConsumerWidget {
  const CourierStatusCard({
    required this.consignmentId,
    required this.provider,
    super.key,
    this.trackingCode,
    this.providerRawStatus,
    this.normalizedStatus,
    this.needsQuantityResolution = false,
  });

  final String consignmentId;
  final String provider;
  final String? trackingCode;
  final String? providerRawStatus;
  final String? normalizedStatus;

  /// The courier said "partly delivered" and gave no numbers, so a person has
  /// to say how many arrived before anything settles.
  final bool needsQuantityResolution;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tracking = ref.watch(consignmentTrackingProvider(consignmentId));

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  _providerName(provider),
                  style: EcomsbdType.sectionTitle,
                ),
              ),
              if (normalizedStatus != null)
                StatusChip(
                  label: _plainLabel(normalizedStatus!),
                  tone: _toneFor(normalizedStatus!),
                ),
            ],
          ),
          if (trackingCode != null) ...<Widget>[
            const SizedBox(height: 6),
            _KeyValue(
              label: context.tr('common.trackingCode'),
              value: trackingCode!,
            ),
          ],
          if (providerRawStatus != null) ...<Widget>[
            const SizedBox(height: 6),
            // The courier's own word, so a support call can quote it.
            _KeyValue(
              label: context.tr('csc.providerSays', <String, Object?>{
                'provider': _providerName(provider),
              }),
              value: providerRawStatus!.replaceAll('_', ' '),
            ),
          ],
          if (needsQuantityResolution) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            const _NeedsQuantities(),
          ],
          const SizedBox(height: EcomsbdSpacing.sm),
          tracking.when(
            loading: () => const SkeletonLoader(height: 48),
            error: (error, _) => error is ApiError && error.isOffline
                ? Text(
                    context.tr('csc.offlineTimeline'),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  )
                : const SizedBox.shrink(),
            data: (value) => _Timeline(tracking: value),
          ),
        ],
      ),
    );
  }
}

/// The seller-facing consequence of `partial_delivered`.
///
/// The courier does not say how many units arrived — its API has no field for
/// it — so ecomsbd asks rather than assuming. Assuming "half" would put a wrong
/// number into realized revenue and stock at the same time.
class _NeedsQuantities extends StatelessWidget {
  const _NeedsQuantities();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: Tone.warning.surface,
        borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr('csc.partialTitle'),
            style: EcomsbdType.label.copyWith(color: Tone.warning.ink),
          ),
          const SizedBox(height: 2),
          Text(context.tr('csc.partialBody'), style: EcomsbdType.caption),
        ],
      ),
    );
  }
}

class _Timeline extends StatelessWidget {
  const _Timeline({required this.tracking});

  final ConsignmentTracking tracking;

  @override
  Widget build(BuildContext context) {
    final unresolved = tracking.unresolvedAttempt;
    final events = tracking.events.take(6).toList();

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (unresolved != null) ...<Widget>[
          _UnresolvedBooking(attempt: unresolved),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
        if (events.isEmpty)
          Text(
            context.tr('csc.noUpdates'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          )
        else ...<Widget>[
          Text(
            context.tr('csc.updatesCaps'),
            style: EcomsbdType.eyebrow.copyWith(color: EcomsbdColors.muted2),
          ),
          const SizedBox(height: 4),
          for (final event in events) _TimelineRow(event: event),
        ],
      ],
    );
  }
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow({required this.event});

  final CourierEvent event;

  @override
  Widget build(BuildContext context) {
    final label = event.statusUndocumented
        // A status this build has never seen documented. Shown as the
        // courier's own words with no interpretation attached.
        ? '${event.rawStatus ?? 'Unknown'} (new to us)'
        : _plainLabel(event.normalizedStatus ?? event.rawStatus ?? '');

    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Container(
            width: 6,
            height: 6,
            margin: const EdgeInsets.only(top: 6, right: 8),
            decoration: BoxDecoration(
              color: EcomsbdColors.muted2,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          Expanded(child: Text(label, style: EcomsbdType.caption)),
          Text(
            // "Checked", not "happened": the courier gives no event time.
            'checked ${_relative(event.lastSeenAt)}',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
      ),
    );
  }
}

/// A booking whose outcome the courier never confirmed.
///
/// Offers no retry, by design. The parcel may exist; ecomsbd is checking.
class _UnresolvedBooking extends StatelessWidget {
  const _UnresolvedBooking({required this.attempt});

  final BookingAttempt attempt;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: Tone.warning.surface,
        borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            attempt.needsAPerson
                ? context.tr('csc.unconfirmedTitle')
                : context.tr('csc.checkingTitle'),
            style: EcomsbdType.label.copyWith(color: Tone.warning.ink),
          ),
          const SizedBox(height: 2),
          Text(
            attempt.needsAPerson
                ? 'We asked Steadfast ${attempt.recoveryAttempts} times and did '
                      'not get a clear answer. Check your Steadfast panel for '
                      'reference ${attempt.merchantReference}, then tell support '
                      'what you found. Do not book this order again until then.'
                : 'Reference ${attempt.merchantReference}. Do not book this '
                      'order again — a second booking would be a second parcel.',
            style: EcomsbdType.caption,
          ),
          if (attempt.recoveryNote != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              attempt.recoveryNote!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }
}

/// Ask the courier to bring a parcel back.
class CourierReturnSheet extends ConsumerStatefulWidget {
  const CourierReturnSheet({required this.consignmentId, super.key});

  final String consignmentId;

  static Future<CourierReturnRequest?> show(
    BuildContext context, {
    required String consignmentId,
  }) {
    return showModalBottomSheet<CourierReturnRequest>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      isDismissible: false,
      builder: (_) => CourierReturnSheet(consignmentId: consignmentId),
    );
  }

  @override
  ConsumerState<CourierReturnSheet> createState() => _CourierReturnSheetState();
}

class _CourierReturnSheetState extends ConsumerState<CourierReturnSheet> {
  final TextEditingController _reason = TextEditingController();
  bool _busy = false;
  bool _confirmed = false;
  CourierReturnRequest? _result;
  ApiError? _error;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    // Guarded twice: the flag stops a second tap that arrives before the
    // rebuild, and `_busy` stops one that arrives after it.
    if (_busy || _result != null) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ref
          .read(courierRepositoryProvider)
          .requestReturn(widget.consignmentId, reason: _reason.text.trim());
      ref.invalidate(courierReturnsProvider(widget.consignmentId));
      ref.invalidate(consignmentTrackingProvider(widget.consignmentId));
      if (mounted) {
        setState(() {
          _result = result;
          _busy = false;
        });
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

    return Padding(
      padding: EdgeInsets.only(bottom: insets),
      // A Material, not a decorated Container: the confirmation control paints
      // its ink on the nearest Material ancestor, and a plain Container would
      // hide it behind its own background.
      child: Material(
        color: EcomsbdColors.backgroundLight,
        borderRadius: const BorderRadius.vertical(
          top: Radius.circular(EcomsbdRadii.lg),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(
            EcomsbdSpacing.lg,
            EcomsbdSpacing.md,
            EcomsbdSpacing.lg,
            EcomsbdSpacing.xl,
          ),
          child: _result != null ? _buildResult(_result!) : _buildForm(),
        ),
      ),
    );
  }

  Widget _buildForm() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _Grip(),
        const SizedBox(height: EcomsbdSpacing.md),
        Text(context.tr('csc.returnTitle'), style: EcomsbdType.sectionTitle),
        const SizedBox(height: 3),
        Text(
          context.tr('csc.returnBody'),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        LabelledField(
          label: context.tr('csc.whyBack'),
          controller: _reason,
          hint: context.tr('csc.whyBackHint'),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        // An explicit confirmation, because this costs money at the courier
        // and cannot be taken back (brief section 35).
        Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: <Widget>[
            Checkbox(
              value: _confirmed,
              onChanged: (value) => setState(() => _confirmed = value ?? false),
            ),
            Expanded(
              child: Text(
                context.tr('csc.confirmReturn'),
                style: EcomsbdType.caption,
              ),
            ),
          ],
        ),
        if (_error != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            _error!.displayMessage,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
          ),
        ],
        const SizedBox(height: EcomsbdSpacing.md),
        Row(
          children: <Widget>[
            Expanded(
              child: OutlinedButton(
                onPressed: _busy ? null : () => Navigator.of(context).pop(),
                child: Text(context.tr('common.cancel')),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: _busy || !_confirmed ? null : _submit,
                style: _primaryButton,
                child: Text(
                  _busy
                      ? context.tr('csc.sending')
                      : context.tr('csc.requestReturn'),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildResult(CourierReturnRequest result) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _Grip(),
        const SizedBox(height: EcomsbdSpacing.md),
        StatusChip(
          label: result.label,
          tone: result.isAmbiguous ? Tone.warning : Tone.good,
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(result.message, style: EcomsbdType.body),
        if (result.isAmbiguous) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            context.tr('csc.dupWarn'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
        const SizedBox(height: EcomsbdSpacing.md),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(result),
          style: _primaryButton,
          child: Text(context.tr('common.gotIt')),
        ),
      ],
    );
  }
}

class _KeyValue extends StatelessWidget {
  const _KeyValue({required this.label, required this.value});

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
        Text(value, style: EcomsbdType.label),
      ],
    );
  }
}

class _Grip extends StatelessWidget {
  const _Grip();

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

/// The courier's name as a seller knows it.
///
/// Courier names stay English in both languages — a seller looking at the
/// Pathao app sees "Pathao". A provider this build has not heard of falls
/// through to its own identifier rather than being hidden: an unknown courier
/// on a parcel is something support needs to see, not something to swallow.
String _providerName(String provider) => switch (provider) {
  'steadfast' => 'Steadfast',
  'pathao' => 'Pathao',
  'redx' => 'RedX',
  'manual' => _t('csc.recordedByHand'),
  _ => provider,
};

/// Plain language for a normalized state.
///
/// Deliberately not the enum name: a seller should never have to read
/// `OUT_FOR_DELIVERY`.
String _plainLabel(String status) => switch (status) {
  'NOT_BOOKED' => _t('status.notBooked'),
  'BOOKING' => _t('status.booking'),
  'BOOKING_UNKNOWN' => _t('book.uncertain'),
  'BOOKED' => _t('cst.withCourier'),
  'PICKED_UP' => _t('csc.pickedUp'),
  'IN_TRANSIT' => _t('rst.onItsWay'),
  'OUT_FOR_DELIVERY' => _t('csc.outForDelivery'),
  'DELIVERED' => _t('status.delivered'),
  'PARTIAL_DELIVERED' => _t('csc.partlyDelivered'),
  'RETURN_REQUESTED' => _t('csc.returnRequested'),
  'RETURNING' => _t('csc.comingBack'),
  'RETURNED' => _t('status.returned'),
  'CANCELLED' => _t('ost.cancelled'),
  'LOST' => _t('csc.lost'),
  'DAMAGED' => _t('sa.damaged'),
  'FAILED' => _t('csc.failed'),
  _ => status.replaceAll('_', ' ').toLowerCase(),
};

Tone _toneFor(String status) => switch (status) {
  'DELIVERED' => Tone.good,
  'PARTIAL_DELIVERED' => Tone.warning,
  'BOOKING_UNKNOWN' => Tone.warning,
  'RETURNED' || 'CANCELLED' || 'LOST' || 'DAMAGED' || 'FAILED' => Tone.bad,
  _ => Tone.info,
};

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
