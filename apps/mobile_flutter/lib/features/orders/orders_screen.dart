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

  static const List<({String? status, String label})> _filters =
      <({String? status, String label})>[
        (status: null, label: 'All'),
        (status: 'DRAFT', label: 'Draft'),
        (status: 'CONFIRMED', label: 'Confirmed'),
        (status: 'PACKED', label: 'Packed'),
        (status: 'FULFILLMENT_STARTED', label: 'With courier'),
        (status: 'COMPLETED', label: 'Completed'),
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
              ? 'Saved on this phone. It will sync when you are back online.'
              : 'Order ${saved.order.orderNumber} saved.',
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
    final topInset = MediaQuery.viewPaddingOf(context).top;

    return Stack(
      children: <Widget>[
        RefreshIndicator(
          onRefresh: _sync,
          child: ListView(
            padding: EdgeInsets.fromLTRB(
              EcomsbdSpacing.page,
              topInset + EcomsbdTouch.minTarget + EcomsbdSpacing.lg,
              EcomsbdSpacing.page,
              EcomsbdSpacing.bottomNavClearance,
            ),
            children: <Widget>[
              const PageHeader(
                eyebrow: 'Order → courier → COD → profit',
                title: 'Orders',
                description: 'Fast seller workflow, not an ERP table.',
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
                hint: 'Order number, name, or full number',
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
                      label: filter.label,
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
                emptyTitle: 'No orders yet',
                emptyMessage:
                    'Type one in, or paste a message from Messenger and check '
                    'what it found.',
                emptyActionLabel: 'Add your first order',
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
                tooltip: 'Paste an order',
                child: const Icon(Icons.content_paste_rounded),
              ),
              const SizedBox(height: EcomsbdSpacing.xs),
              FloatingActionButton.extended(
                heroTag: 'new-order',
                onPressed: _compose,
                backgroundColor: EcomsbdColors.orange,
                foregroundColor: Colors.white,
                icon: const Icon(Icons.add_rounded),
                label: const Text('New order'),
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
          ? 'Not yet numbered'
          : order.orderNumber,
      customerName: order.customerName?.isNotEmpty == true
          ? order.customerName!
          : (order.customerPhoneMasked ?? 'Customer'),
      status: StatusChip(
        label: order.statusLabel,
        tone: orderStatusTone(order.status),
      ),
      maskedPhone: order.customerPhoneMasked,
      area: order.deliveryArea,
      itemSummary: order.itemSummary,
      onTap: onTap,
      facts: <OrderFact>[
        OrderFact(label: 'COD', value: order.codAmount.format()),
        OrderFact(
          label: 'Courier',
          value: fulfillmentLabel(order.fulfillmentState),
        ),
        OrderFact(label: 'Risk', value: riskLabel(order.riskState)),
        OrderFact(label: 'Profit', value: profitLabel(order.profitState)),
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
      provider: '$count change${count == 1 ? '' : 's'} waiting',
      detail: 'Saved on this phone and not on the server yet.',
      tone: Tone.warning,
      actionLabel: 'Sync now',
      onAction: onSync,
    );
  }
}
