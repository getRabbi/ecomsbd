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
import '../../l10n/app_strings.dart';

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
              title: context.tr('common.couldNotLoad'),
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
          ProviderHealthBanner(
            provider: context.tr('od.conflictTitle'),
            detail: context.tr('od.conflictBody'),
            tone: Tone.bad,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
        ] else if (syncState == LocalSyncState.localOnly) ...<Widget>[
          ProviderHealthBanner(
            provider: context.tr('od.notSyncedTitle'),
            detail: context.tr('od.notSyncedBody'),
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
                          : (order.customerPhoneMasked ??
                                context.tr('common.customer')),
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
        SectionHeader(title: context.tr('od.items')),
        GlassCard(
          child: Column(
            children: <Widget>[
              for (final item in order.items) ...<Widget>[
                _ItemLine(item: item),
                if (item != order.items.last)
                  const Divider(height: EcomsbdSpacing.lg),
              ],
              const Divider(height: EcomsbdSpacing.lg),
              _MoneyLine(
                label: context.tr('od.itemsTotal'),
                amount: order.subtotal,
              ),
              if (!order.discount.isZero)
                _MoneyLine(
                  label: context.tr('od.discount'),
                  amount: -order.discount,
                ),
              if (!order.deliveryFee.isZero)
                _MoneyLine(
                  label: context.tr('od.deliveryFee'),
                  amount: order.deliveryFee,
                ),
              const SizedBox(height: EcomsbdSpacing.xs),
              _MoneyLine(
                label: context.tr('od.codToCollect'),
                amount: order.codAmount,
                strong: true,
              ),
            ],
          ),
        ),
        if (order.note?.isNotEmpty == true) ...<Widget>[
          SectionHeader(title: context.tr('common.note')),
          GlassCard(child: Text(order.note!, style: EcomsbdType.body)),
        ],
        if (order.sourceText?.isNotEmpty == true) ...<Widget>[
          SectionHeader(
            title: context.tr('od.originalMessage'),
            subtitle: context.tr('od.originalMessageSub'),
          ),
          GlassCard(
            child: Text(
              order.sourceText!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
        if (history != null) ...<Widget>[
          SectionHeader(title: context.tr('od.otherOrders')),
          history.when(
            loading: () => SkeletonLoader.card(height: 60),
            error: (_, __) => Text(
              context.tr('od.otherOrdersError'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            data: (orders) {
              final others = orders
                  .where((other) => other.id != order.id)
                  .toList();
              if (others.isEmpty) {
                return Text(
                  context.tr('od.firstOrder'),
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
          SectionHeader(title: context.tr('od.moveOn')),
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
            context.tr('od.noCourierContact'),
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
            label: context.tr('orders.fact.courier'),
            value: fulfillmentLabel(order.fulfillmentState),
            tone: Tone.neutral,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _StateLine(
            icon: Icons.shield_outlined,
            label: context.tr('od.deliveryRisk'),
            value: riskLabel(order.riskState),
            tone: Tone.neutral,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _StateLine(
            icon: Icons.savings_outlined,
            label: context.tr('orders.fact.profit'),
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
      title: Text(context.tr('od.cancelTitle')),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            context.tr('od.cancelBody'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          TextField(
            controller: _reason,
            decoration: InputDecoration(
              labelText: context.tr('common.reason'),
              hintText: context.tr('od.cancelReasonHint'),
            ),
          ),
        ],
      ),
      actions: <Widget>[
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: Text(context.tr('od.keepIt')),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(
            _reason.text.trim().isEmpty
                ? context.tr('od.cancelledBySeller')
                : _reason.text.trim(),
          ),
          child: Text(context.tr('status.cancelOrder')),
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
    final canBook = ref.watch(canBookWithAnyCourierProvider);
    final courierReady = canBook.maybeWhen(
      data: (value) => value,
      orElse: () => false,
    );

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SectionHeader(
          title: context.tr('od.sendIt'),
          subtitle: courierReady
              ? context.tr('od.sendItSub')
              : context.tr('od.manualMode'),
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
            label: Text(context.tr('book.title')),
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
            label: Text(context.tr('od.recordByHand')),
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
            label: Text(context.tr('od.handToCourier')),
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
