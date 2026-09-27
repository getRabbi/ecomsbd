import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/orders_repository.dart';
import '../../data/couriers/courier_providers.dart';
import '../../design/components/badges.dart';
import '../../design/components/order_card.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../couriers/courier_compare_screen.dart';
import '../risk/risk_review_sheet.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import 'courier_booking_sheet.dart';
import 'order_compose_screen.dart';
import 'order_detail_screen.dart';
import 'order_status.dart';

/// The Orders tab.
///
/// Master spec section 3: a fast seller workflow, not an ERP table. Each row
/// carries the facts that decide what to do next, with courier state and money
/// state shown separately — a parcel can be delivered while its COD is still
/// unpaid, and merging the two hides unpaid money (section 1.4).
///
/// The cells for courier, risk and profit read "Not booked", "Not checked" and
/// "Pending" because that is what is true in Phase B. None of them is filled
/// with a plausible-looking placeholder.
class OrdersScreen extends ConsumerStatefulWidget {
  const OrdersScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  ConsumerState<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends ConsumerState<OrdersScreen> {
  /// Where the floating paste / new-order stack sits, just above the nav.
  static const double _fabBottom = EcomsbdSpacing.bottomNavClearance - 12;

  /// Where the list ends: past the top of the floating stack (the small FAB
  /// padded to a 48dp target, the gap, the 56dp FAB), so the last card's
  /// actions scroll clear of both buttons instead of stopping beneath them.
  static const double _listEndClearance =
      _fabBottom +
      EcomsbdTouch.minTarget +
      EcomsbdSpacing.sm +
      56 +
      EcomsbdSpacing.sm;

  final TextEditingController _search = TextEditingController();

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<void> _compose({bool paste = false}) async {
    final saved = await Navigator.of(context).push<SavedOrder>(
      MaterialPageRoute<SavedOrder>(
        builder: (_) => OrderComposeScreen(startWithPaste: paste),
      ),
    );
    if (saved == null || !mounted) {
      return;
    }
    await ref.read(orderListProvider.notifier).refresh();
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          saved.isQueued
              // Said plainly, because the seller cannot quote a number that
              // does not exist yet to a courier.
              ? context.tr('orders.savedOffline')
              : context.tr('orders.savedNumber', <String, Object?>{
                  'number': saved.order.orderNumber,
                }),
        ),
      ),
    );
  }

  Future<void> _openOrder(SellerOrder order) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => OrderDetailScreen(orderId: order.id),
      ),
    );
    if (mounted) {
      await ref.read(orderListProvider.notifier).refresh();
    }
  }

  /// The order detail's own compare-then-book flow, started from the card.
  /// Without a connected courier there is nothing to book through here, so the
  /// order opens instead, where it can be sent by hand.
  Future<void> _book(SellerOrder order) async {
    var ready = false;
    try {
      final couriers = await ref.read(bookableCouriersProvider.future);
      ready = couriers.any((courier) => courier.bookable);
    } on ApiError {
      ready = false;
    }
    if (!mounted) return;
    if (!ready) {
      await _openOrder(order);
      return;
    }
    final provider = await CourierCompareScreen.open(context, order: order);
    if (provider == null || !mounted) return;
    final booked = await CourierBookingSheet.show(
      context,
      order: order,
      provider: provider,
    );
    if (booked != null && mounted) {
      await ref.read(orderListProvider.notifier).refresh();
    }
  }

  Future<void> _sync() async {
    await ref.read(syncControllerProvider.notifier).sync();
    if (mounted) {
      await ref.read(orderListProvider.notifier).refresh();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(orderListProvider);
    final controller = ref.read(orderListProvider.notifier);
    final pending = ref.watch(pendingMutationCountProvider).valueOrNull ?? 0;

    return Stack(
      children: <Widget>[
        RefreshIndicator(
          // Without this the spinner lands at y=0, behind the floating glass
          // top bar, where the seller never sees it.
          edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
          onRefresh: _sync,
          child: ListView(
            padding: EdgeInsets.fromLTRB(
              EcomsbdSpacing.page,
              EcomsbdLayout.shellTopPadding(context),
              EcomsbdSpacing.page,
              _listEndClearance,
            ),
            children: <Widget>[
              PageHeader(
                eyebrow: context.tr('orders.eyebrow'),
                title: context.tr('orders.title'),
                description: context.tr('orders.description'),
              ),
              // Whether the app is online is the app-wide banner's job; this
              // is only what has not reached the server yet.
              if (pending > 0) ...<Widget>[
                _PendingBanner(count: pending, onSync: _sync),
                const SizedBox(height: EcomsbdSpacing.sm),
              ],
              if (state.isStale) ...<Widget>[
                StaleDataNotice(
                  fetchedAt: state.fetchedAt,
                  onRetry: controller.refresh,
                ),
                const SizedBox(height: EcomsbdSpacing.sm),
              ],
              CommerceSearchField(
                controller: _search,
                hint: context.tr('orders.searchHint'),
                onChanged: controller.setSearch,
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              SizedBox(
                height: EcomsbdTouch.minTarget,
                // Grouped over the server's statuses, which stay unchanged.
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: OrderFilterGroup.values.length,
                  separatorBuilder: (_, __) =>
                      const SizedBox(width: EcomsbdSpacing.xs),
                  itemBuilder: (context, index) {
                    final group = OrderFilterGroup.values[index];
                    return FilterToggle(
                      label: context.tr('ordg.${group.name}'),
                      selected:
                          controller.status == null &&
                          controller.group == group,
                      onChanged: (_) => controller.setGroup(group),
                    );
                  },
                ),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              PagedListBody<SellerOrder>(
                state: state,
                onRetry: controller.refresh,
                onLoadMore: controller.loadMore,
                emptyIcon: Icons.receipt_long_outlined,
                emptyTitle: context.tr('orders.emptyTitle'),
                emptyMessage: context.tr('orders.emptyBody'),
                emptyActionLabel: context.tr('orders.emptyAction'),
                onEmptyAction: _compose,
                itemBuilder: (context, order) => SellerOrderCard(
                  order: order,
                  onTap: () => _openOrder(order),
                  onBook: () => _book(order),
                  onReviewRisk: () =>
                      RiskReviewSheet.review(context, order: order),
                ),
              ),
            ],
          ),
        ),
        Positioned(
          right: EcomsbdSpacing.md,
          bottom: _fabBottom,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              FloatingActionButton.small(
                heroTag: 'paste-order',
                onPressed: () => _compose(paste: true),
                backgroundColor: Colors.white,
                foregroundColor: EcomsbdColors.ink,
                elevation: 2,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(13),
                  side: const BorderSide(color: EcomsbdColors.line),
                ),
                tooltip: context.tr('orders.pasteTooltip'),
                child: const Icon(Icons.content_paste_rounded),
              ),
              const SizedBox(height: EcomsbdSpacing.sm),
              // `.fab`: the prototype's square orange add button.
              FloatingActionButton(
                heroTag: 'new-order',
                onPressed: _compose,
                backgroundColor: EcomsbdColors.orange,
                foregroundColor: Colors.white,
                elevation: 6,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(18),
                ),
                tooltip: context.tr('orders.newOrder'),
                child: const Icon(Icons.add_rounded, size: 28),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// An order row on the feed.
///
/// Collapsed to what decides the next step — COD, courier booking and return
/// risk — with profit and the full breakdown one tap away on the detail. An
/// order ready to send offers "Compare & book courier"; a risky one offers
/// its risk review.
class SellerOrderCard extends StatelessWidget {
  const SellerOrderCard({
    required this.order,
    super.key,
    this.onTap,
    this.onBook,
    this.onReviewRisk,
  });

  final SellerOrder order;
  final VoidCallback? onTap;
  final VoidCallback? onBook;
  final VoidCallback? onReviewRisk;

  @override
  Widget build(BuildContext context) {
    final risk = order.riskState;
    final risky = risk == 'HIGH' || risk == 'MEDIUM';
    final readyToSend =
        (order.status == 'CONFIRMED' || order.status == 'PACKED') &&
        order.fulfillmentState == 'NOT_BOOKED';

    return OrderCard(
      reference: order.orderNumber == 'PENDING'
          ? context.tr('orders.notNumbered')
          : order.orderNumber,
      customerName: order.customerName?.isNotEmpty == true
          ? order.customerName!
          : (order.customerPhoneMasked ?? context.tr('common.customer')),
      status: StatusChip(
        label: order.statusLabel,
        tone: orderStatusTone(order.status),
      ),
      maskedPhone: order.customerPhoneMasked,
      area: order.deliveryArea,
      itemSummary: order.itemSummary,
      onTap: onTap,
      facts: <OrderFact>[
        OrderFact(
          label: context.tr('orders.fact.cod'),
          value: order.codAmount.format(),
        ),
        OrderFact(
          label: context.tr('orders.fact.courier'),
          value: fulfillmentLabel(order.fulfillmentState),
        ),
      ],
      // Risk is a quiet note until it is real; then it is a pill.
      note: risky ? null : riskLabel(risk),
      alert: risky
          ? RiskPill(
              label: riskLabel(risk),
              tone: risk == 'HIGH' ? Tone.bad : Tone.warning,
            )
          : null,
      actions: <Widget>[
        if (readyToSend && onBook != null)
          CardButton(
            label: context.tr('ord.compareBook'),
            icon: Icons.local_shipping_outlined,
            onPressed: onBook,
          ),
        if (risky && onReviewRisk != null)
          CardButton(
            label: context.tr('ord.reviewRisk'),
            primary: false,
            onPressed: onReviewRisk,
          )
        else if (readyToSend && onBook != null && onTap != null)
          Tooltip(
            message: context.tr('ord.open'),
            child: CardButton(label: '•••', primary: false, onPressed: onTap),
          ),
      ],
    );
  }
}

/// Work waiting to reach the server, with a way to send it now.
class _PendingBanner extends StatelessWidget {
  const _PendingBanner({required this.count, required this.onSync});

  final int count;
  final VoidCallback onSync;

  @override
  Widget build(BuildContext context) {
    return ProviderHealthBanner(
      provider: context.trPlural('orders.pendingWaiting', count),
      detail: context.tr('orders.pendingDetail'),
      tone: Tone.warning,
      actionLabel: context.tr('orders.syncNow'),
      onAction: onSync,
    );
  }
}
