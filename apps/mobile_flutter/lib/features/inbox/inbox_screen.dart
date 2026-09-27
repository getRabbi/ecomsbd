import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/channels/channel_models.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/orders_repository.dart';
import '../../design/components/badges.dart';
import '../../design/components/cards.dart';
import '../../design/components/navigation.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../messaging/messaging_screen.dart';
import '../orders/order_compose_screen.dart';
import '../orders/order_detail_screen.dart';
import '../settings/connections_screen.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';

/// The Inbox tab: customer conversations from every channel, and the step
/// from a message to an order.
///
/// Conversations come from an [InboxSource]. None can read inbound chats yet,
/// so the list says that plainly rather than showing an empty inbox as if no
/// customer wrote. Turning a message into an order works today: it uses the
/// same server parser as "paste an order", and the seller reviews every field
/// before anything is saved.
class InboxScreen extends ConsumerStatefulWidget {
  const InboxScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  ConsumerState<InboxScreen> createState() => _InboxScreenState();
}

class _InboxScreenState extends ConsumerState<InboxScreen> {
  final TextEditingController _message = TextEditingController();

  SalesChannel? _channel;
  bool _detecting = false;
  ApiError? _error;
  ParsedOrder? _parsed;
  Customer? _known;

  @override
  void dispose() {
    _message.dispose();
    super.dispose();
  }

  Future<void> _detect() async {
    final text = _message.text.trim();
    if (text.isEmpty || _detecting) return;
    setState(() {
      _detecting = true;
      _error = null;
      _parsed = null;
      _known = null;
    });
    try {
      final parsed = await ref.read(ordersRepositoryProvider).parse(text);
      Customer? known;
      final phone = parsed.selectedPhone;
      if (phone != null && phone.isNotEmpty) {
        try {
          known = await ref
              .read(customersRepositoryProvider)
              .lookupByPhone(phone);
        } on ApiError {
          // A failed lookup only means no history is shown.
        }
      }
      if (mounted) {
        setState(() {
          _parsed = parsed;
          _known = known;
        });
      }
    } on ApiError catch (error) {
      if (mounted) setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _detecting = false);
    }
  }

  Future<void> _createOrder() async {
    final parsed = _parsed;
    if (parsed == null) return;
    final saved = await Navigator.of(context).push<SavedOrder>(
      MaterialPageRoute<SavedOrder>(
        builder: (_) => OrderComposeScreen(initialParsed: parsed),
      ),
    );
    if (saved == null || !mounted) return;
    setState(() {
      _parsed = null;
      _known = null;
      _message.clear();
    });
    await ref.read(orderListProvider.notifier).refresh();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          saved.isQueued
              ? context.tr('orders.savedOffline')
              : context.tr('orders.savedNumber', <String, Object?>{
                  'number': saved.order.orderNumber,
                }),
        ),
      ),
    );
  }

  Future<void> _openThread(InboxThread thread) async {
    final text = await InboxThreadSheet.show(context, thread);
    if (text == null || !mounted) return;
    _message.text = text;
    await _detect();
  }

  @override
  Widget build(BuildContext context) {
    final threads = ref.watch(inboxThreadsProvider);
    final channels = ref.watch(salesChannelsProvider);

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        ref.invalidate(inboxThreadsProvider);
        ref.invalidate(salesChannelsProvider);
        await ref.read(inboxThreadsProvider.future);
      },
      child: ListView(
        padding: EdgeInsets.fromLTRB(
          EcomsbdSpacing.page,
          EcomsbdLayout.shellTopPadding(context),
          EcomsbdSpacing.page,
          EcomsbdSpacing.bottomNavClearance,
        ),
        children: <Widget>[
          PageHeader(
            eyebrow: context.tr('inbox.eyebrow'),
            title: context.tr('inbox.title'),
            description: context.tr('inbox.description'),
          ),
          SizedBox(
            height: EcomsbdTouch.minTarget,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: <Widget>[
                FilterToggle(
                  label: context.tr('ordg.all'),
                  selected: _channel == null,
                  onChanged: (_) => setState(() => _channel = null),
                ),
                for (final channel in channelDisplayOrder) ...<Widget>[
                  const SizedBox(width: EcomsbdSpacing.xs),
                  FilterToggle(
                    label: channelName(context, channel),
                    selected: _channel == channel,
                    onChanged: (_) => setState(() => _channel = channel),
                  ),
                ],
              ],
            ),
          ),
          SectionHeader(
            title: context.tr('inbox.waiting'),
            subtitle: context.tr('inbox.waitingSub'),
          ),
          threads.when(
            loading: () => const ContentLoader(minHeight: 90),
            error: (_, __) => _NotSyncedCard(onManage: _manageChannels),
            data: (list) {
              if (list == null) {
                return _NotSyncedCard(onManage: _manageChannels);
              }
              final shown = list
                  .where((t) => _channel == null || t.channel == _channel)
                  .toList();
              if (shown.isEmpty) {
                return EmptyState(
                  icon: Icons.forum_outlined,
                  title: context.tr('inbox.emptyTitle'),
                  message: context.tr('inbox.emptyBody'),
                );
              }
              return Column(
                children: <Widget>[
                  for (final thread in shown)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 9),
                      child: ConversationRow(
                        thread: thread,
                        onTap: () => unawaited(_openThread(thread)),
                      ),
                    ),
                ],
              );
            },
          ),
          SectionHeader(
            title: context.tr('inbox.detectTitle'),
            subtitle: context.tr('inbox.detectSub'),
          ),
          GlassCard(
            padding: const EdgeInsets.fromLTRB(14, 8, 14, 14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                TextField(
                  controller: _message,
                  minLines: 3,
                  maxLines: 6,
                  decoration: InputDecoration(
                    hintText: context.tr('inbox.detectHint'),
                    border: InputBorder.none,
                    isDense: true,
                  ),
                ),
                const SizedBox(height: EcomsbdSpacing.xs),
                FilledButton.icon(
                  onPressed: _detecting ? null : () => unawaited(_detect()),
                  icon: _detecting
                      ? const SizedBox.square(
                          dimension: 16,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.auto_awesome_outlined, size: 18),
                  label: Text(context.tr('inbox.detect')),
                  style: FilledButton.styleFrom(
                    backgroundColor: EcomsbdColors.orange,
                    minimumSize: const Size.fromHeight(46),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                    textStyle: EcomsbdType.label.copyWith(
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ),
              ],
            ),
          ),
          if (_error != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              _error!.displayMessage,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          ],
          if (_parsed != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            DetectedOrderCard(
              parsed: _parsed!,
              known: _known,
              onCreate: () => unawaited(_createOrder()),
            ),
          ],
          // Each row opens the channels screen; no separate Manage link.
          SectionHeader(
            title: context.tr('inbox.channels'),
            subtitle: context.tr('inbox.channelsSub'),
          ),
          channels.maybeWhen(
            data: (list) => ListCard(
              children: <Widget>[
                for (final status in inDisplayOrder(list))
                  if (_channel == null || status.channel == _channel)
                    channelListRow(context, status, onTap: _manageChannels),
              ],
            ),
            orElse: () => Text(
              context.tr('conn.unavailable'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          const SizedBox(height: 10),
          ListCard(
            children: <Widget>[
              ListCardRow(
                icon: Icons.send_outlined,
                title: context.tr('inbox.updates'),
                subtitle: context.tr('inbox.updatesSub'),
                onTap: () => unawaited(
                  Navigator.of(context).push(
                    MaterialPageRoute<void>(
                      builder: (_) => const MessagingScreen(),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  void _manageChannels() {
    unawaited(
      Navigator.of(context).push(
        MaterialPageRoute<void>(builder: (_) => const ConnectionsScreen()),
      ),
    );
  }
}

class _NotSyncedCard extends StatelessWidget {
  const _NotSyncedCard({required this.onManage});

  final VoidCallback onManage;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.fromLTRB(13, 10, 6, 10),
      child: Row(
        children: <Widget>[
          const SoftIcon(
            icon: Icons.sync_disabled_rounded,
            tone: Tone.neutral,
            size: 36,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              context.tr('inbox.notSynced'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          TextButton(
            onPressed: onManage,
            child: Text(context.tr('conn.manage')),
          ),
        ],
      ),
    );
  }
}

/// "Order details detected from chat", ready for the seller to check.
class DetectedOrderCard extends StatelessWidget {
  const DetectedOrderCard({
    required this.parsed,
    required this.onCreate,
    super.key,
    this.known,
  });

  final ParsedOrder parsed;
  final Customer? known;
  final VoidCallback onCreate;

  @override
  Widget build(BuildContext context) {
    final missing = context.tr('inbox.notFound');
    final quantity = parsed.items.fold<int>(0, (sum, i) => sum + i.quantity);
    final product = parsed.items.isEmpty
        ? missing
        : parsed.items.length == 1
        ? parsed.items.first.displayName
        : '${parsed.items.first.displayName} +${parsed.items.length - 1}';
    final fields = <(String, String, bool)>[
      (
        context.tr('inbox.f.name'),
        parsed.customerName ?? missing,
        parsed.isUncertain('name'),
      ),
      (
        context.tr('inbox.f.phone'),
        parsed.selectedPhone ??
            (parsed.phones.isEmpty ? missing : parsed.phones.join(' / ')),
        parsed.isUncertain('phone') || parsed.needsPhoneSelection,
      ),
      (
        context.tr('inbox.f.address'),
        parsed.address ?? missing,
        parsed.isUncertain('address'),
      ),
      (context.tr('inbox.f.product'), product, parsed.items.isEmpty),
      (
        context.tr('inbox.f.quantity'),
        quantity == 0 ? missing : '$quantity',
        quantity == 0,
      ),
      (
        context.tr('inbox.f.cod'),
        parsed.codAmountPaisa == null
            ? missing
            : Money(parsed.codAmountPaisa!).format(),
        parsed.isUncertain('amount'),
      ),
    ];

    return CustomPaint(
      foregroundPainter: const _DashedBorderPainter(
        color: EcomsbdColors.detectBorder,
        radius: 18,
      ),
      child: Container(
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: EcomsbdColors.detectFill,
          borderRadius: BorderRadius.circular(18),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Row(
              children: <Widget>[
                const Icon(
                  Icons.auto_awesome,
                  size: 16,
                  color: EcomsbdColors.detectInk,
                ),
                const SizedBox(width: 7),
                Expanded(
                  child: Text(
                    context.tr('inbox.detected'),
                    style: EcomsbdType.bodyStrong.copyWith(
                      fontSize: 13,
                      fontWeight: FontWeight.w800,
                      color: EcomsbdColors.detectInk,
                    ),
                  ),
                ),
                StatusChip(
                  label: parsed.isLowConfidence
                      ? context.tr('inbox.lowConfidence')
                      : context.tr('inbox.checkFirst'),
                  tone: parsed.isLowConfidence ? Tone.warning : Tone.info,
                ),
              ],
            ),
            const SizedBox(height: 10),
            LayoutBuilder(
              builder: (context, constraints) {
                const gap = 7.0;
                final half = (constraints.maxWidth - gap) / 2;
                return Wrap(
                  spacing: gap,
                  runSpacing: gap,
                  children: <Widget>[
                    for (final (label, value, unsure) in fields)
                      SizedBox(
                        // Address and product read better on a full row.
                        width:
                            label == context.tr('inbox.f.address') ||
                                label == context.tr('inbox.f.product')
                            ? constraints.maxWidth
                            : half,
                        child: _DetectCell(
                          label: label,
                          value: value,
                          unsure: unsure,
                        ),
                      ),
                  ],
                );
              },
            ),
            for (final warning in parsed.warnings) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                warning,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
              ),
            ],
            if (known != null) ...<Widget>[
              const SizedBox(height: 10),
              CustomerContextLine(customer: known!),
            ],
            const SizedBox(height: 11),
            FilledButton.icon(
              onPressed: onCreate,
              icon: const Icon(Icons.add_rounded, size: 18),
              label: Text(context.tr('inbox.createOrder')),
              style: FilledButton.styleFrom(
                backgroundColor: EcomsbdColors.orange,
                minimumSize: const Size.fromHeight(46),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14),
                ),
                textStyle: EcomsbdType.label.copyWith(
                  fontWeight: FontWeight.w800,
                ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              context.tr('inbox.reviewNote'),
              textAlign: TextAlign.center,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
            ),
          ],
        ),
      ),
    );
  }
}

/// One detected field: a small uppercase label over its value.
class _DetectCell extends StatelessWidget {
  const _DetectCell({
    required this.label,
    required this.value,
    required this.unsure,
  });

  final String label;
  final String value;

  /// The parser was not sure; the value is shown in amber to be checked.
  final bool unsure;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: EcomsbdColors.detectCellBorder),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(
            label.toUpperCase(),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: trackedFor(
              label,
              EcomsbdType.eyebrow.copyWith(
                color: const Color(0xFF8B98A6),
                letterSpacing: 0.5,
              ),
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            style: EcomsbdType.bodyStrong.copyWith(
              fontSize: 12.5,
              color: unsure ? EcomsbdColors.amber : null,
            ),
          ),
        ],
      ),
    );
  }
}

/// The dashed outline of the detected-order panel.
class _DashedBorderPainter extends CustomPainter {
  const _DashedBorderPainter({required this.color, required this.radius});

  final Color color;
  final double radius;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.5;
    final path = Path()
      ..addRRect(
        RRect.fromRectAndRadius(
          Offset.zero & size,
          Radius.circular(radius),
        ).deflate(0.75),
      );
    for (final metric in path.computeMetrics()) {
      for (var d = 0.0; d < metric.length; d += 9) {
        canvas.drawPath(metric.extractPath(d, d + 5), paint);
      }
    }
  }

  @override
  bool shouldRepaint(_DashedBorderPainter oldDelegate) =>
      oldDelegate.color != color || oldDelegate.radius != radius;
}

/// `.inbox-row` — avatar, name, last message, channel and time, unread count.
class ConversationRow extends StatelessWidget {
  const ConversationRow({required this.thread, super.key, this.onTap});

  final InboxThread thread;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final unread = thread.unreadCount;
    return Material(
      color: Colors.white,
      borderRadius: BorderRadius.circular(18),
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(18),
        child: Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: EcomsbdColors.line),
          ),
          child: Row(
            children: <Widget>[
              Container(
                width: 44,
                height: 44,
                alignment: Alignment.center,
                decoration: const BoxDecoration(
                  shape: BoxShape.circle,
                  gradient: LinearGradient(
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                    colors: <Color>[Color(0xFF153B61), Color(0xFF0F1D2E)],
                  ),
                ),
                child: Text(
                  thread.customerLabel.characters.first.toUpperCase(),
                  style: EcomsbdType.bodyStrong.copyWith(
                    color: Colors.white,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      thread.customerLabel,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: EcomsbdType.bodyStrong,
                    ),
                    Text(
                      thread.lastMessage,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: EcomsbdType.body.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                    Text(
                      '${channelName(context, thread.channel)} · '
                      '${formatRelative(thread.lastMessageAt)}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: EcomsbdType.caption.copyWith(
                        fontSize: 11,
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              if (unread > 0) ...<Widget>[
                const SizedBox(width: 8),
                Container(
                  constraints: const BoxConstraints(minWidth: 21),
                  height: 21,
                  padding: const EdgeInsets.symmetric(horizontal: 5),
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: EcomsbdColors.orange,
                    borderRadius: BorderRadius.circular(11),
                  ),
                  child: Text(
                    unread > 99 ? '99+' : '$unread',
                    style: EcomsbdType.chip.copyWith(
                      fontSize: 11,
                      fontWeight: FontWeight.w800,
                      color: Colors.white,
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// A customer's delivery record in one line, toned by returns.
class CustomerContextLine extends StatelessWidget {
  const CustomerContextLine({required this.customer, super.key});

  final Customer customer;

  @override
  Widget build(BuildContext context) {
    final returned = customer.returnedCount;
    final tone = customer.isBlocked || returned >= 2
        ? Tone.bad
        : (returned == 1 ? Tone.warning : Tone.good);
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: tone.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: <Widget>[
          Icon(tone.icon, size: 18, color: tone.ink),
          const SizedBox(width: EcomsbdSpacing.xs),
          Expanded(
            child: Text(
              context.tr('inbox.customerLine', <String, Object?>{
                'name': customer.displayName,
                'orders': customer.orderCount,
                'delivered': customer.deliveredCount,
                'returned': returned,
              }),
              style: EcomsbdType.caption.copyWith(color: tone.ink),
            ),
          ),
        ],
      ),
    );
  }
}

/// One conversation with the customer's record beside it.
///
/// Resolves to the message text when the seller asks to turn it into an
/// order; the Inbox runs detection on it.
class InboxThreadSheet extends ConsumerWidget {
  const InboxThreadSheet({required this.thread, super.key});

  final InboxThread thread;

  static Future<String?> show(
    BuildContext context,
    InboxThread thread,
  ) => GlassBottomSheet.show<String>(
    context: context,
    title: thread.customerLabel,
    description:
        '${channelName(context, thread.channel)} · ${formatRelative(thread.lastMessageAt)}',
    child: InboxThreadSheet(thread: thread),
  );

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final customerId = thread.customerId;
    final customer = customerId == null
        ? null
        : ref.watch(customerProvider(customerId)).valueOrNull;
    final orders = customerId == null
        ? null
        : ref.watch(customerOrdersProvider(customerId)).valueOrNull;
    final orderId = thread.orderId;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        GlassCard(
          padding: const EdgeInsets.all(EcomsbdSpacing.sm),
          child: Text(thread.lastMessage, style: EcomsbdType.body),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
        if (customer != null)
          CustomerContextLine(customer: customer)
        else
          Text(
            context.tr('inbox.unknownCustomer'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        if (orders != null && orders.isNotEmpty) ...<Widget>[
          SectionHeader(title: context.tr('rrv.recent')),
          for (final order in orders.take(3))
            GlassListRow(
              title: order.orderNumber,
              subtitle: order.statusLabel,
              trailingTop: order.codAmount.format(),
            ),
        ],
        const SizedBox(height: EcomsbdSpacing.sm),
        if (orderId != null)
          OutlinedButton(
            onPressed: () {
              Navigator.of(context).pop();
              unawaited(
                Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => OrderDetailScreen(orderId: orderId),
                  ),
                ),
              );
            },
            child: Text(context.tr('inbox.openOrder')),
          )
        else
          FilledButton(
            onPressed: () => Navigator.of(context).pop(thread.lastMessage),
            style: FilledButton.styleFrom(
              backgroundColor: EcomsbdColors.orange,
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
            ),
            child: Text(context.tr('inbox.fromChat')),
          ),
      ],
    );
  }
}
