import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/orders_repository.dart';
import '../../design/components/badges.dart';
import '../../design/components/order_card.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
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
  final TextEditingController _search = TextEditingController();

  /// The status values are API contract and are never translated; only the
  /// chip label the seller reads is.
  static const List<({String? status, String labelKey})> _filters =
      <({String? status, String labelKey})>[
        (status: null, labelKey: 'orders.filter.all'),
        (status: 'DRAFT', labelKey: 'orders.filter.draft'),
        (status: 'CONFIRMED', labelKey: 'orders.filter.confirmed'),
        (status: 'PACKED', labelKey: 'orders.filter.packed'),
        (status: 'FULFILLMENT_STARTED', labelKey: 'orders.filter.withCourier'),
        (status: 'COMPLETED', labelKey: 'orders.filter.completed'),
      ];

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
    final isOffline = ref.watch(isOfflineProvider);

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
              EcomsbdSpacing.bottomNavClearance,
            ),
            children: <Widget>[
              PageHeader(
                eyebrow: context.tr('orders.eyebrow'),
                title: context.tr('orders.title'),
                description: context.tr('orders.description'),
              ),
              if (isOffline) ...<Widget>[
                OfflineBanner(pendingCount: pending, onRetry: _sync),
                const SizedBox(height: EcomsbdSpacing.sm),
              ] else if (pending > 0) ...<Widget>[
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
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  itemCount: _filters.length,
                  separatorBuilder: (_, __) =>
                      const SizedBox(width: EcomsbdSpacing.xs),
                  itemBuilder: (context, index) {
                    final filter = _filters[index];
                    return FilterToggle(
                      label: context.tr(filter.labelKey),
                      selected: controller.status == filter.status,
                      onChanged: (_) => controller.setStatus(filter.status),
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
                ),
              ),
            ],
          ),
        ),
        Positioned(
          right: EcomsbdSpacing.md,
          bottom: EcomsbdSpacing.bottomNavClearance - 12,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.end,
            children: <Widget>[
              FloatingActionButton.small(
                heroTag: 'paste-order',
                onPressed: () => _compose(paste: true),
                backgroundColor: Colors.white,
                foregroundColor: EcomsbdColors.ink,
                tooltip: context.tr('orders.pasteTooltip'),
                child: const Icon(Icons.content_paste_rounded),
              ),
              const SizedBox(height: EcomsbdSpacing.xs),
              FloatingActionButton.extended(
                heroTag: 'new-order',
                onPressed: _compose,
                backgroundColor: EcomsbdColors.orange,
                foregroundColor: Colors.white,
                icon: const Icon(Icons.add_rounded),
                label: Text(context.tr('orders.newOrder')),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// An order row on the feed.
class SellerOrderCard extends StatelessWidget {
  const SellerOrderCard({required this.order, super.key, this.onTap});

  final SellerOrder order;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
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
        OrderFact(
          label: context.tr('orders.fact.risk'),
          value: riskLabel(order.riskState),
        ),
        OrderFact(
          label: context.tr('orders.fact.profit'),
          value: profitLabel(order.profitState),
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
