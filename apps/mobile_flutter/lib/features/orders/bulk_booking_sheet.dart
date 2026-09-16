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
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';

/// Book several orders at once.
///
/// Brief section 33. What this screen is careful about:
///
/// * **Partial success is shown, never hidden.** A batch where 18 booked, one
///   was refused and one is unconfirmed reads as exactly that. Rolling it up
///   into "20 orders processed" would bury the one that needs attention.
/// * **The three outcomes are visually distinct.** "Failed safely" and
///   "Checking result" mean opposite things for what the seller should do next,
///   and a shared red chip would make them look the same.
/// * **There is no bulk retry.** Refused orders can be fixed individually;
///   unconfirmed ones must not be touched at all.
class BulkBookingSheet extends ConsumerStatefulWidget {
  const BulkBookingSheet({required this.orders, super.key});

  final List<SellerOrder> orders;

  static Future<BookingReport?> show(
    BuildContext context, {
    required List<SellerOrder> orders,
  }) {
    return showModalBottomSheet<BookingReport>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      isDismissible: false,
      enableDrag: false,
      builder: (_) => BulkBookingSheet(orders: orders),
    );
  }

  @override
  ConsumerState<BulkBookingSheet> createState() => _BulkBookingSheetState();
}

class _BulkBookingSheetState extends ConsumerState<BulkBookingSheet> {
  bool _busy = false;
  BookingReport? _report;
  ApiError? _error;

  /// Orders this app can tell, before sending anything, that the courier will
  /// refuse. Catching them here means the batch that goes out is the batch that
  /// can succeed, and the seller sees why the rest were held back.
  late final List<SellerOrder> _bookable = widget.orders
      .where(_isBookable)
      .toList();
  late final List<SellerOrder> _notBookable = widget.orders
      .where((order) => !_isBookable(order))
      .toList();

  static bool _isBookable(SellerOrder order) {
    if (order.status == 'CANCELLED' || order.status == 'COMPLETED') {
      return false;
    }
    if ((order.deliveryAddress ?? '').trim().isEmpty) {
      return false;
    }
    return order.fulfillmentState == 'NOT_BOOKED';
  }

  Future<void> _submit() async {
    if (_busy || _report != null) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final report = await ref
          .read(courierRepositoryProvider)
          .bookBulk(_bookable.map((order) => order.id).toList());
      ref.invalidate(moneySummaryProvider);
      ref.invalidate(homeMetricsProvider);
      await ref.read(orderListProvider.notifier).refresh();
      if (mounted) {
        setState(() {
          _report = report;
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
    return Container(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.85,
      ),
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
      child: _report != null ? _buildResults(_report!) : _buildReview(),
    );
  }

  Widget _buildReview() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _Grip(),
        const SizedBox(height: EcomsbdSpacing.md),
        Text(context.tr('book.title'), style: EcomsbdType.sectionTitle),
        const SizedBox(height: 3),
        Text(
          '${_bookable.length} of ${widget.orders.length} selected orders can '
          'be booked now.',
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
        if (_notBookable.isNotEmpty) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
          Container(
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
                  '${_notBookable.length} held back',
                  style: EcomsbdType.label.copyWith(color: Tone.warning.ink),
                ),
                const SizedBox(height: 2),
                Text(
                  context.tr('bulk.heldBackNote'),
                  style: EcomsbdType.caption,
                ),
              ],
            ),
          ),
        ],
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
                onPressed: _busy ? null : () => Navigator.of(context).pop(),
                child: Text(context.tr('common.cancel')),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            Expanded(
              flex: 2,
              child: FilledButton(
                onPressed: _busy || _bookable.isEmpty ? null : _submit,
                style: _primaryButton,
                child: Text(
                  _busy
                      ? context.tr('status.booking')
                      : 'Book ${_bookable.length} orders',
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildResults(BookingReport report) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        const _Grip(),
        const SizedBox(height: EcomsbdSpacing.md),
        Text(context.tr('bulk.results'), style: EcomsbdType.sectionTitle),
        const SizedBox(height: EcomsbdSpacing.sm),
        Wrap(
          spacing: EcomsbdSpacing.xs,
          runSpacing: EcomsbdSpacing.xs,
          children: <Widget>[
            StatusChip(label: '${report.booked} booked', tone: Tone.good),
            if (report.failed > 0)
              StatusChip(
                label: '${report.failed} failed safely',
                tone: Tone.bad,
              ),
            if (report.ambiguous > 0)
              StatusChip(
                label: '${report.ambiguous} checking result',
                tone: Tone.warning,
              ),
          ],
        ),
        if (report.hasAmbiguous) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.sm),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(EcomsbdSpacing.sm),
            decoration: BoxDecoration(
              color: Tone.warning.surface,
              borderRadius: BorderRadius.circular(EcomsbdRadii.sm),
            ),
            child: Text(
              context.tr('booking.ambiguousFallback'),
              style: EcomsbdType.caption,
            ),
          ),
        ],
        const SizedBox(height: EcomsbdSpacing.sm),
        Flexible(
          child: ListView.builder(
            shrinkWrap: true,
            itemCount: report.items.length,
            itemBuilder: (context, index) =>
                _ResultRow(item: report.items[index]),
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(report),
          style: _primaryButton,
          child: Text(context.tr('common.done')),
        ),
      ],
    );
  }
}

class _ResultRow extends StatelessWidget {
  const _ResultRow({required this.item});

  final BookingItem item;

  @override
  Widget build(BuildContext context) {
    final tone = switch (item.outcome) {
      BookingOutcome.booked => Tone.good,
      BookingOutcome.ambiguous => Tone.warning,
      BookingOutcome.failed => Tone.bad,
    };

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  item.merchantReference.isEmpty
                      ? context.tr('book.order')
                      : item.merchantReference,
                  style: EcomsbdType.label,
                ),
                if (item.trackingCode != null)
                  Text(
                    item.trackingCode!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted2,
                    ),
                  )
                else if (item.message != null)
                  Text(
                    item.message!,
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          StatusChip(label: item.outcome.label, tone: tone, showIcon: false),
        ],
      ),
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

final ButtonStyle _primaryButton = FilledButton.styleFrom(
  backgroundColor: EcomsbdColors.orange,
  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
  shape: const StadiumBorder(),
  textStyle: EcomsbdType.label,
);
