import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/couriers/courier_providers.dart';
import '../../data/local/tables.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import 'courier_booking_sheet.dart';
import 'dispatch_sheet.dart';
import 'order_status.dart';

/// One order in full.
///
/// Courier state, risk and profit are shown as three separate facts and none of
/// them is invented. Until the engines that produce them exist, they read
/// "Not booked", "Not checked" and "Pending" — master spec section 1.4 is
/// explicit that a parcel can be delivered while its money is still unpaid, and
/// collapsing those into one status is what hides unpaid money from a seller.
class OrderDetailScreen extends ConsumerStatefulWidget {
  const OrderDetailScreen({required this.orderId, super.key});

  final String orderId;

  @override
  ConsumerState<OrderDetailScreen> createState() => _OrderDetailScreenState();
}

class _OrderDetailScreenState extends ConsumerState<OrderDetailScreen> {
  LocalSyncState _syncState = LocalSyncState.synced;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _loadSyncState();
  }

  Future<void> _loadSyncState() async {
    final state = await ref
        .read(ordersRepositoryProvider)
        .syncStateOf(widget.orderId);
    if (mounted) {
      setState(() => _syncState = state);
    }
  }

  Future<void> _transition(SellerOrder order, String status) async {
    String? reason;
    if (status == 'CANCELLED') {
      reason = await _CancelDialog.show(context);
      if (reason == null) {
        return;
      }
    }
    setState(() => _busy = true);
    try {
      await ref
          .read(ordersRepositoryProvider)
          .update(
            order.id,
            status: status,
            cancellationReason: reason,
            expectedVersion: order.version,
          );
      ref.invalidate(orderProvider(order.id));
      await ref.read(orderListProvider.notifier).refresh();
      await _loadSyncState();
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

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(orderProvider(widget.orderId));

    return async.when(
      loading: () => DetailScaffold(
        title: 'Order',
        children: <Widget>[SkeletonLoader.card(height: 200)],
      ),
      error: (error, _) => DetailScaffold(
        title: 'Order',
        children: <Widget>[
          if (error is ApiError)
            ErrorStateCard(
              error: error,
              onRetry: () => ref.invalidate(orderProvider(widget.orderId)),
            )
          else
            EmptyState(
              icon: Icons.error_outline,
              title: 'Could not load',
              message: '$error',
            ),
        ],
      ),
      data: (order) => _OrderBody(
        order: order,
        syncState: _syncState,
        busy: _busy,
        onTransition: (status) => _transition(order, status),
      ),
    );
  }
}

class _OrderBody extends ConsumerWidget {
  const _OrderBody({
    required this.order,
    required this.syncState,
    required this.busy,
    required this.onTransition,
  });

  final SellerOrder order;
  final LocalSyncState syncState;
  final bool busy;
  final ValueChanged<String> onTransition;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final next = nextStatuses(order.status);
    final history = order.customerId == null
        ? null
        : ref.watch(customerOrdersProvider(order.customerId!));

    return DetailScaffold(
      title: order.orderNumber,
      subtitle: '${order.statusLabel} · ${formatRelative(order.createdAt)}',
      actions: <Widget>[SyncBadge(state: syncState)],
      children: <Widget>[
        if (syncState == LocalSyncState.conflict) ...<Widget>[
          const ProviderHealthBanner(
            provider: 'Changed in two places',
            detail:
                'This order was edited somewhere else while your change was '
                'waiting. Nothing was overwritten — open it again once you are '
                'online to choose which version to keep.',
            tone: Tone.bad,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ] else if (syncState == LocalSyncState.localOnly) ...<Widget>[
          const ProviderHealthBanner(
            provider: 'Not on the server yet',
            detail:
                'Saved on this phone. It will sync when you are back online, '
                'and it will keep the same order.',
            tone: Tone.warning,
            actionLabel: null,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ],
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      order.customerName?.isNotEmpty == true
                          ? order.customerName!
                          : (order.customerPhoneMasked ?? 'Customer'),
                      style: EcomsbdType.sectionTitle,
                    ),
                  ),
                  StatusChip(
                    label: order.statusLabel,
                    tone: orderStatusTone(order.status),
                  ),
                ],
              ),
              if (order.customerPhoneMasked != null) ...<Widget>[
                const SizedBox(height: 2),
                Text(
                  order.customerPhoneMasked!,
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
              if (order.deliveryAddress?.isNotEmpty == true) ...<Widget>[
                const SizedBox(height: EcomsbdSpacing.sm),
                // The seller's own words. A courier's normalisation never
                // replaces this (master spec section 12).
                Text(order.deliveryAddress!, style: EcomsbdType.body),
              ],
              if (order.deliveryArea != null ||
                  order.deliveryDistrict != null) ...<Widget>[
                const SizedBox(height: 3),
                Text(
                  <String?>[
                    order.deliveryArea,
                    order.deliveryDistrict,
                  ].whereType<String>().join(', '),
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        // Four separate facts. None of them is a guess.
        OrderStateGrid(order: order),
        const SectionHeader(title: 'Items'),
        GlassCard(
          child: Column(
            children: <Widget>[
              for (final item in order.items) ...<Widget>[
                _ItemLine(item: item),
                if (item != order.items.last)
                  const Divider(height: EcomsbdSpacing.lg),
              ],
              const Divider(height: EcomsbdSpacing.lg),
              _MoneyLine(label: 'Items total', amount: order.subtotal),
              if (!order.discount.isZero)
                _MoneyLine(label: 'Discount', amount: -order.discount),
              if (!order.deliveryFee.isZero)
                _MoneyLine(label: 'Delivery fee', amount: order.deliveryFee),
              const SizedBox(height: EcomsbdSpacing.xs),
              _MoneyLine(
                label: 'COD to collect',
                amount: order.codAmount,
                strong: true,
              ),
            ],
          ),
        ),
        if (order.note?.isNotEmpty == true) ...<Widget>[
          const SectionHeader(title: 'Note'),
          GlassCard(child: Text(order.note!, style: EcomsbdType.body)),
        ],
        if (order.sourceText?.isNotEmpty == true) ...<Widget>[
          const SectionHeader(
            title: 'Original message',
            subtitle: 'Kept exactly as it was pasted',
          ),
          GlassCard(
            child: Text(
              order.sourceText!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
        if (history != null) ...<Widget>[
          const SectionHeader(title: 'This customer’s other orders'),
          history.when(
            loading: () => SkeletonLoader.card(height: 60),
            error: (_, __) => Text(
              'Could not load their history.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            data: (orders) {
              final others = orders
                  .where((other) => other.id != order.id)
                  .toList();
              if (others.isEmpty) {
                return Text(
                  'This is their first order with you.',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                );
              }
              return Column(
                children: <Widget>[
                  for (final other in others)
                    GlassListRow(
                      title: other.orderNumber,
                      subtitle:
                          '${other.statusLabel} · ${formatRelative(other.createdAt)}',
                      trailingTop: other.codAmount.format(),
                    ),
                ],
              );
            },
          ),
        ],
        if (next.isNotEmpty) ...<Widget>[
          const SectionHeader(title: 'Move this order on'),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              for (final status in next)
                OutlinedButton(
                  onPressed: busy ? null : () => onTransition(status),
                  style: OutlinedButton.styleFrom(
                    minimumSize: const Size(0, EcomsbdTouch.minTarget),
                    shape: const StadiumBorder(),
                    foregroundColor: status == 'CANCELLED'
                        ? EcomsbdColors.red
                        : EcomsbdColors.ink,
                    textStyle: EcomsbdType.label,
                  ),
                  child: Text(statusActionLabel(status)),
                ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            'None of these contacts a courier.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ],
        if (order.status == 'PACKED' ||
            order.status == 'CONFIRMED') ...<Widget>[
          _SendItSection(order: order, busy: busy),
        ],
      ],
    );
  }
}

/// The four facts an order carries, kept separate.
class OrderStateGrid extends StatelessWidget {
  const OrderStateGrid({required this.order, super.key});

  final SellerOrder order;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          _StateLine(
            icon: Icons.local_shipping_outlined,
            label: 'Courier',
            value: fulfillmentLabel(order.fulfillmentState),
            tone: Tone.neutral,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _StateLine(
            icon: Icons.shield_outlined,
            label: 'Delivery risk',
            value: riskLabel(order.riskState),
            tone: Tone.neutral,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _StateLine(
            icon: Icons.savings_outlined,
            label: 'Profit',
            value: profitLabel(order.profitState),
            tone: Tone.neutral,
          ),
        ],
      ),
    );
  }
}

class _StateLine extends StatelessWidget {
  const _StateLine({
    required this.icon,
    required this.label,
    required this.value,
    required this.tone,
  });

  final IconData icon;
  final String label;
  final String value;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        Icon(icon, size: 17, color: EcomsbdColors.muted),
        const SizedBox(width: EcomsbdSpacing.sm),
        Expanded(
          child: Text(
            label,
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ),
        StatusChip(label: value, tone: tone, showIcon: false),
      ],
    );
  }
}

class _ItemLine extends StatelessWidget {
  const _ItemLine({required this.item});

  final OrderItem item;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text(item.productName, style: EcomsbdType.bodyStrong),
              const SizedBox(height: 2),
              Text(
                '${item.quantity} × ${item.unitPrice.format()}'
                '${item.variantLabel == null ? '' : ' · ${item.variantLabel}'}',
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
              ),
            ],
          ),
        ),
        MoneyText(item.lineTotal, style: EcomsbdType.bodyStrong),
      ],
    );
  }
}

class _MoneyLine extends StatelessWidget {
  const _MoneyLine({
    required this.label,
    required this.amount,
    this.strong = false,
  });

  final String label;
  final Money amount;
  final bool strong;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: strong
                  ? EcomsbdType.bodyStrong
                  : EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          MoneyText(
            amount,
            style: strong ? EcomsbdType.sectionTitle : EcomsbdType.body,
          ),
        ],
      ),
    );
  }
}

class _CancelDialog extends StatefulWidget {
  const _CancelDialog();

  static Future<String?> show(BuildContext context) => showDialog<String>(
    context: context,
    builder: (_) => const _CancelDialog(),
  );

  @override
  State<_CancelDialog> createState() => _CancelDialogState();
}

class _CancelDialogState extends State<_CancelDialog> {
  final TextEditingController _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Cancel this order?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            'Any stock reserved for it goes back. The order stays in your '
            'records with the reason.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          TextField(
            controller: _reason,
            decoration: const InputDecoration(
              labelText: 'Reason',
              hintText: 'Customer changed their mind',
            ),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Keep it'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(
            _reason.text.trim().isEmpty
                ? 'Cancelled by seller'
                : _reason.text.trim(),
          ),
          child: const Text('Cancel order'),
        ),
      ],
    );
  }
}

/// How a parcel leaves the shop.
///
/// Two routes, and which one is offered depends on what the shop has connected
/// rather than on which is "better". Manual mode is always present: it is the
/// path every courier failure degrades to, and a seller who has not connected
/// an account is not in a lesser state (brief section 46).
class _SendItSection extends ConsumerWidget {
  const _SendItSection({required this.order, required this.busy});

  final SellerOrder order;
  final bool busy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final canBook = ref.watch(canBookWithCourierProvider);
    final courierReady = canBook.maybeWhen(
      data: (value) => value,
      orElse: () => false,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SectionHeader(
          title: 'Send it',
          subtitle: courierReady
              ? 'Book with Steadfast, or record a courier by hand'
              : 'Manual courier mode — nothing is sent to a provider',
        ),
        if (courierReady) ...<Widget>[
          FilledButton.icon(
            onPressed: busy
                ? null
                : () async {
                    final result = await CourierBookingSheet.show(
                      context,
                      order: order,
                    );
                    if (result != null) {
                      ref.invalidate(orderProvider(order.id));
                    }
                  },
            icon: const Icon(Icons.local_shipping_outlined, size: 18),
            label: const Text('Book with Steadfast'),
            style: FilledButton.styleFrom(
              backgroundColor: EcomsbdColors.orange,
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
              textStyle: EcomsbdType.label,
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          OutlinedButton.icon(
            onPressed: busy
                ? null
                : () async {
                    final sent = await DispatchSheet.show(
                      context,
                      orderId: order.id,
                    );
                    if (sent) {
                      ref.invalidate(orderProvider(order.id));
                    }
                  },
            icon: const Icon(Icons.edit_note_rounded, size: 18),
            label: const Text('Record a courier by hand'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
              textStyle: EcomsbdType.label,
            ),
          ),
        ] else
          FilledButton.icon(
            onPressed: busy
                ? null
                : () async {
                    final sent = await DispatchSheet.show(
                      context,
                      orderId: order.id,
                    );
                    if (sent) {
                      ref.invalidate(orderProvider(order.id));
                    }
                  },
            icon: const Icon(Icons.local_shipping_outlined, size: 18),
            label: const Text('Hand to a courier'),
            style: FilledButton.styleFrom(
              backgroundColor: EcomsbdColors.orange,
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
              textStyle: EcomsbdType.label,
            ),
          ),
      ],
    );
  }
}
