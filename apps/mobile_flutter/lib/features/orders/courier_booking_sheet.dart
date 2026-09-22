import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/analytics/analytics_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/couriers/models.dart';
import '../../data/money/money_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';

/// Book a parcel with a courier.
///
/// Brief section 32. The rules this sheet exists to obey:
///
/// * **One tap, one booking.** The confirm button disables on the first press
///   and never re-enables inside a submission. A double tap on a slow
///   connection is the cheapest way to ship two parcels.
/// * **An unconfirmed result is not a failure.** It gets its own screen state,
///   its own Bangla copy, and — deliberately — **no retry button**. A seller
///   who can press "try again" after a lost answer will, and the courier will
///   happily create a second parcel.
/// * **Only fields the courier documents.** There is no weight, parcel size or
///   insurance value here: Steadfast V1 documents none, and a field the courier
///   ignores misleads the seller who filled it in.
class CourierBookingSheet extends ConsumerStatefulWidget {
  const CourierBookingSheet({required this.order, super.key});

  final SellerOrder order;

  static Future<BookingItem?> show(
    BuildContext context, {
    required SellerOrder order,
  }) {
    return showModalBottomSheet<BookingItem>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      // A booking is in flight behind this sheet; dismissing it by tapping
      // outside would hide an outcome the seller has to see.
      isDismissible: false,
      enableDrag: false,
      builder: (_) => CourierBookingSheet(order: order),
    );
  }

  @override
  ConsumerState<CourierBookingSheet> createState() =>
      _CourierBookingSheetState();
}

enum _Stage { review, submitting, done }

class _CourierBookingSheetState extends ConsumerState<CourierBookingSheet> {
  final TextEditingController _note = TextEditingController();
  final TextEditingController _description = TextEditingController();

  _Stage _stage = _Stage.review;
  int _deliveryType = 0;

  /// The courier this booking goes to. Chosen from the couriers the server
  /// says are bookable, never defaulted to a hard-coded provider name.
  String? _provider;
  BookingItem? _result;
  ApiError? _error;

  @override
  void dispose() {
    _note.dispose();
    _description.dispose();
    super.dispose();
  }

  Future<void> _confirm() async {
    if (_stage != _Stage.review) {
      return;
    }
    final courier = _selectedCourier();
    final provider = courier?.provider;
    if (courier == null || provider == null || !courier.bookable) {
      // Nothing to book with. The button is already disabled in this state;
      // this guard exists so a race cannot send a booking to a courier the
      // server just told us is unavailable.
      return;
    }
    setState(() {
      _stage = _Stage.submitting;
      _error = null;
    });
    try {
      final report = await ref
          .read(courierRepositoryProvider)
          .book(
            widget.order.id,
            provider: provider,
            note: _note.text.trim(),
            itemDescription: _description.text.trim(),
            // Only sent for a courier that declares it. Sending Steadfast's
            // 0/1 to a courier with different codes would book the wrong
            // service class.
            deliveryType: courier.supportsDeliveryType ? _deliveryType : null,
          );
      final item = report.items.isEmpty ? null : report.items.first;
      _invalidateAfterBooking();
      if (mounted) {
        setState(() {
          _result = item;
          _stage = _Stage.done;
        });
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _error = error;
          // An ambiguous error is a *terminal* state for this sheet, not
          // something to return to the form and let the seller re-submit.
          _stage = error.code == ApiErrorCode.bookingAmbiguous
              ? _Stage.done
              : _Stage.review;
        });
      }
    }
  }

  /// The courier currently selected, or the first bookable one.
  ///
  /// Resolved against the live list every time rather than stored once, so a
  /// courier that stopped being bookable while the sheet was open cannot be
  /// booked from a stale selection.
  ///
  /// [watch] must be true anywhere the result is rendered: with `read` alone
  /// this widget never rebuilds when the courier list arrives, so the sheet
  /// opens saying "the courier", with Confirm disabled, and stays that way
  /// until the seller happens to tap a courier. `read` is right only in the
  /// submit handler, which runs once and must not subscribe to anything.
  BookableCourier? _selectedCourier({bool watch = false}) {
    final async = watch
        ? ref.watch(bookableCouriersProvider)
        : ref.read(bookableCouriersProvider);
    final rows = async.value ?? const <BookableCourier>[];
    if (rows.isEmpty) {
      return null;
    }
    for (final row in rows) {
      if (row.provider == _provider) {
        return row;
      }
    }
    for (final row in rows) {
      if (row.bookable) {
        return row;
      }
    }
    return null;
  }

  void _invalidateAfterBooking() {
    ref.invalidate(orderProvider(widget.order.id));
    ref.invalidate(moneySummaryProvider);
    ref.invalidate(homeMetricsProvider);
    ref.read(orderListProvider.notifier).refresh();
  }

  @override
  Widget build(BuildContext context) {
    final insets = MediaQuery.viewInsetsOf(context).bottom;

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
        child: switch (_stage) {
          _Stage.review => _buildReview(),
          _Stage.submitting => _buildSubmitting(),
          _Stage.done => _buildDone(),
        },
      ),
    );
  }

  /// The courier's name for copy, or a neutral word before one is resolved.
  String get _courierName =>
      _selectedCourier(watch: true)?.displayName ??
      AppStrings(activeAppLocale).t('common.courier');

  Widget _buildReview() {
    final selected = _selectedCourier(watch: true);
    final order = widget.order;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _SheetGrip(),
        const SizedBox(height: EcomsbdSpacing.md),
        Text(
          context.tr('book.title', <String, Object?>{'provider': _courierName}),
          style: EcomsbdType.sectionTitle,
        ),
        const SizedBox(height: 3),
        Text(
          context.tr('book.body', <String, Object?>{'provider': _courierName}),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        _ReviewRow(label: context.tr('book.order'), value: order.orderNumber),
        _ReviewRow(
          label: context.tr('book.recipient'),
          value: order.customerName ?? context.tr('common.customer'),
        ),
        // Masked, as everywhere else in the app. The courier gets the full
        // number; this screen does not need to show it (master spec 101).
        _ReviewRow(
          label: context.tr('common.phone'),
          value: order.customerPhoneMasked ?? '—',
        ),
        _ReviewRow(
          label: context.tr('common.address'),
          value: order.deliveryAddress ?? context.tr('book.noAddress'),
        ),
        _ReviewRow(
          label: context.tr('book.collectCod'),
          value: order.codAmount.format(),
          emphasis: true,
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        _CourierPicker(
          selected: selected?.provider,
          onChanged: (provider) => setState(() => _provider = provider),
        ),
        // Shown only for a courier that declares it, so this generic sheet
        // never has to know which couriers have which service classes.
        if (selected?.supportsDeliveryType ?? false) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.md),
          Text(context.tr('book.deliveryType'), style: EcomsbdType.label),
          const SizedBox(height: EcomsbdSpacing.xs),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            children: <Widget>[
              FilterToggle(
                label: context.tr('book.homeDelivery'),
                selected: _deliveryType == 0,
                onChanged: (_) => setState(() => _deliveryType = 0),
              ),
              FilterToggle(
                label: context.tr('book.hubPickup'),
                selected: _deliveryType == 1,
                onChanged: (_) => setState(() => _deliveryType = 1),
              ),
            ],
          ),
        ],
        const SizedBox(height: EcomsbdSpacing.md),
        LabelledField(
          label: context.tr('book.contents'),
          controller: _description,
          hint: context.tr('book.contentsHint'),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        LabelledField(
          label: context.tr('book.noteForCourier'),
          controller: _note,
          hint: context.tr('book.noteHint'),
        ),
        if (_error != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
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
                onPressed: () => Navigator.of(context).pop(),
                child: Text(context.tr('common.cancel')),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            Expanded(
              flex: 2,
              child: FilledButton(
                // No bookable courier means no booking. Manual courier mode
                // is how a parcel still ships in that state.
                onPressed: (selected?.bookable ?? false) ? _confirm : null,
                style: _primaryButton,
                child: Text(context.tr('book.confirm')),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildSubmitting() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _SheetGrip(),
        const SizedBox(height: EcomsbdSpacing.lg),
        Row(
          children: <Widget>[
            const SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Text(context.tr('status.booking'), style: EcomsbdType.sectionTitle),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(
          context.tr('book.waiting', <String, Object?>{
            'provider': _courierName,
          }),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.xl),
      ],
    );
  }

  Widget _buildDone() {
    final result = _result;
    final ambiguous =
        result?.outcome == BookingOutcome.ambiguous ||
        _error?.code == ApiErrorCode.bookingAmbiguous;

    if (ambiguous) {
      return _AmbiguousOutcome(
        courierName: _courierName,
        message: result?.message ?? _error?.displayMessage,
        onClose: () => Navigator.of(context).pop(result),
      );
    }

    if (result != null && result.outcome == BookingOutcome.booked) {
      return _BookedOutcome(
        courierName: _courierName,
        item: result,
        onClose: () => Navigator.of(context).pop(result),
      );
    }

    return _FailedOutcome(
      courierName: _courierName,
      message:
          result?.message ??
          _error?.displayMessage ??
          context.tr('book.rejected', <String, Object?>{
            'provider': _courierName,
          }),
      onRetry: () => setState(() => _stage = _Stage.review),
      onClose: () => Navigator.of(context).pop(result),
    );
  }
}

/// The screen the whole safety model exists for.
///
/// There is no retry button here, and there must never be one. The parcel may
/// already exist; ecomsbd is checking with the courier using the same reference
/// it sent, and a second booking would be a second real parcel with a real
/// delivery charge.
class _AmbiguousOutcome extends StatelessWidget {
  const _AmbiguousOutcome({
    required this.courierName,
    required this.onClose,
    this.message,
  });

  final String courierName;

  final String? message;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _SheetGrip(),
        const SizedBox(height: EcomsbdSpacing.md),
        StatusChip(label: context.tr('book.uncertain'), tone: Tone.warning),
        const SizedBox(height: EcomsbdSpacing.sm),
        // The server owns the wording of a money-adjacent message (master spec
        // section 46), so its copy is the headline when it sent one. The
        // fallback is the same sentence, for the case where it did not.
        Text(
          message ?? context.tr('booking.ambiguousFallback'),
          style: EcomsbdType.body,
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        // Explains *why*, without repeating the sentence above it.
        Text(
          context.tr('book.uncertainBody', <String, Object?>{
            'provider': courierName,
          }),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        // Deliberately a single, non-destructive action. No "try again".
        FilledButton(
          onPressed: onClose,
          style: _primaryButton,
          child: Text(context.tr('common.gotIt')),
        ),
      ],
    );
  }
}

class _BookedOutcome extends StatelessWidget {
  const _BookedOutcome({
    required this.courierName,
    required this.item,
    required this.onClose,
  });

  final String courierName;

  final BookingItem item;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _SheetGrip(),
        const SizedBox(height: EcomsbdSpacing.md),
        StatusChip(label: context.tr('status.booked'), tone: Tone.good),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(
          context.tr('book.hasParcel', <String, Object?>{
            'provider': courierName,
          }),
          style: EcomsbdType.sectionTitle,
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        _ReviewRow(
          label: context.tr('common.reference'),
          value: item.merchantReference,
        ),
        if (item.trackingCode != null)
          _ReviewRow(
            label: context.tr('common.trackingCode'),
            value: item.trackingCode!,
          ),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(
          context.tr('book.doneBody'),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        FilledButton(
          onPressed: onClose,
          style: _primaryButton,
          child: Text(context.tr('common.done')),
        ),
      ],
    );
  }
}

/// A booking the courier positively refused.
///
/// This one *does* get a retry, and safely: the provider answered and said no,
/// so nothing was created and the same reference can be used again.
class _FailedOutcome extends StatelessWidget {
  const _FailedOutcome({
    required this.courierName,
    required this.message,
    required this.onRetry,
    required this.onClose,
  });

  final String courierName;
  final String message;
  final VoidCallback onRetry;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _SheetGrip(),
        const SizedBox(height: EcomsbdSpacing.md),
        StatusChip(label: context.tr('status.notBooked'), tone: Tone.bad),
        const SizedBox(height: EcomsbdSpacing.sm),
        Text(
          context.tr('book.notAccepted', <String, Object?>{
            'provider': courierName,
          }),
          style: EcomsbdType.body,
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        Text(
          message,
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        Text(
          context.tr('book.safeToRetry'),
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
        ),
        const SizedBox(height: EcomsbdSpacing.md),
        Row(
          children: <Widget>[
            Expanded(
              child: OutlinedButton(
                onPressed: onClose,
                child: Text(context.tr('common.close')),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: onRetry,
                style: _primaryButton,
                child: Text(context.tr('book.changeAndRetry')),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Choose the courier this parcel goes to.
///
/// Nothing here names a courier. The list, the display names, whether each one
/// can take a booking and the first thing to fix when it cannot all come from
/// `/couriers/bookable`, so adding a courier changes no code in this file.
///
/// Couriers that cannot take a booking are shown rather than hidden, with the
/// reason. Hiding them would leave a seller wondering where Pathao went; saying
/// "choose a pickup store" tells them what to do. A courier with nothing to
/// connect at all is absent from the server's list entirely.
class _CourierPicker extends ConsumerWidget {
  const _CourierPicker({required this.selected, required this.onChanged});

  final String? selected;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final couriers = ref.watch(bookableCouriersProvider);

    return couriers.when(
      loading: () => SkeletonLoader.card(height: 64),
      error: (error, _) => error is ApiError
          ? ErrorStateCard(
              error: error,
              onRetry: () => ref.invalidate(bookableCouriersProvider),
            )
          : EmptyState(
              icon: Icons.error_outline,
              title: context.tr('common.couldNotLoad'),
              message: '$error',
            ),
      data: (rows) {
        if (rows.isEmpty) {
          return EmptyState(
            icon: Icons.local_shipping_outlined,
            title: context.tr('book.noCouriers'),
            message: context.tr('book.noCouriersBody'),
          );
        }
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Text(context.tr('book.courier'), style: EcomsbdType.label),
            const SizedBox(height: EcomsbdSpacing.xs),
            for (final courier in rows) ...<Widget>[
              _CourierOption(
                courier: courier,
                selected: courier.provider == selected,
                onTap: courier.bookable
                    ? () => onChanged(courier.provider)
                    : null,
              ),
              const SizedBox(height: 6),
            ],
          ],
        );
      },
    );
  }
}

class _CourierOption extends StatelessWidget {
  const _CourierOption({
    required this.courier,
    required this.selected,
    this.onTap,
  });

  final BookableCourier courier;
  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final blocked = !courier.bookable;

    return Material(
      type: MaterialType.transparency,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(EcomsbdSpacing.sm),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
            border: Border.all(
              color: selected ? EcomsbdColors.orange : EcomsbdColors.stroke,
              width: selected ? 1.5 : 1,
            ),
            color: selected ? EcomsbdColors.orangeSoft : null,
          ),
          child: Row(
            children: <Widget>[
              Icon(
                selected
                    ? Icons.radio_button_checked
                    : Icons.radio_button_unchecked,
                size: 18,
                color: blocked
                    ? EcomsbdColors.muted2
                    : (selected ? EcomsbdColors.orange : EcomsbdColors.muted),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      courier.displayName,
                      style: EcomsbdType.body.copyWith(
                        color: blocked ? EcomsbdColors.muted : null,
                      ),
                    ),
                    if (blocked && courier.block != null)
                      Text(
                        // The server's stable code, rendered here in the
                        // seller's language.
                        courier.block!.label,
                        style: EcomsbdType.caption.copyWith(
                          color: courier.block!.isFixableInSettings
                              ? EcomsbdColors.amber
                              : EcomsbdColors.muted2,
                        ),
                      )
                    else if (courier.storeName != null)
                      Text(
                        courier.storeName!,
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
      ),
    );
  }
}

class _ReviewRow extends StatelessWidget {
  const _ReviewRow({
    required this.label,
    required this.value,
    this.emphasis = false,
  });

  final String label;
  final String value;
  final bool emphasis;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          SizedBox(
            width: 96,
            child: Text(
              label,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: emphasis ? EcomsbdType.sectionTitle : EcomsbdType.label,
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
