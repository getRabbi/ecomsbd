import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/channels/channel_models.dart';
import '../../data/chat_orders/chat_orders.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/list_controllers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/orders_repository.dart';
import '../../design/components/badges.dart';
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
import 'chat_order_widgets.dart';

/// The Inbox tab: order automation for Messenger and WhatsApp, not a chat app.
///
/// Customers keep chatting in Messenger or WhatsApp. The server reads each
/// burst of messages into one draft order with the same parser as "paste a
/// message", and the seller reviews, edits and confirms it here — or ignores
/// it. Nothing becomes an order without that confirmation. Pasting a message
/// by hand still works, for chats from anywhere else.
class InboxScreen extends ConsumerStatefulWidget {
  const InboxScreen({super.key, this.onNavigate});

  final ValueChanged<String>? onNavigate;

  @override
  ConsumerState<InboxScreen> createState() => _InboxScreenState();
}

class _InboxScreenState extends ConsumerState<InboxScreen> {
  final TextEditingController _message = TextEditingController();

  SalesChannel? _channel;

  /// `review` (ready and needs-info), `ready` or `needs_info`.
  String _status = 'review';
  final Set<String> _acting = <String>{};
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

  void _refreshChat() {
    ref.invalidate(chatDraftsProvider);
    ref.invalidate(chatOrderSummaryProvider);
    ref.invalidate(chatAttentionProvider);
  }

  Future<void> _review(ChatDraft draft) async {
    final saved = await Navigator.of(context).push<SavedOrder>(
      MaterialPageRoute<SavedOrder>(
        builder: (_) => OrderComposeScreen(chatDraft: draft),
      ),
    );
    if (saved == null || !mounted) return;
    _refreshChat();
    await ref.read(orderListProvider.notifier).refresh();
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          context.tr('orders.savedNumber', <String, Object?>{
            'number': saved.order.orderNumber,
          }),
        ),
      ),
    );
  }

  Future<void> _ignore(ChatDraft draft) async {
    setState(() => _acting.add(draft.id));
    try {
      await ref.read(chatOrdersRepositoryProvider).ignore(draft.id);
      _refreshChat();
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(context.tr('cho.ignored'))));
      }
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    } finally {
      if (mounted) setState(() => _acting.remove(draft.id));
    }
  }

  Future<void> _resolve(ChatAttentionItem item) async {
    try {
      await ref.read(chatOrdersRepositoryProvider).resolveAttention(item.id);
      ref.invalidate(chatAttentionProvider);
    } on ApiError catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(error.displayMessage)));
      }
    }
  }

  /// The chat provider a channel filter shows; null shows every one.
  String? get _provider => switch (_channel) {
    SalesChannel.facebook => 'MESSENGER',
    SalesChannel.whatsapp => 'WHATSAPP',
    null => null,
    _ => 'NONE',
  };

  @override
  Widget build(BuildContext context) {
    final drafts = ref.watch(chatDraftsProvider(_status));
    final summary = ref.watch(chatOrderSummaryProvider).valueOrNull;
    final attention = ref.watch(chatAttentionProvider).valueOrNull;
    final channels = ref.watch(salesChannelsProvider);
    final provider = _provider;

    return RefreshIndicator(
      edgeOffset: EcomsbdLayout.shellRefreshOffset(context),
      onRefresh: () async {
        _refreshChat();
        ref.invalidate(salesChannelsProvider);
        await ref.read(chatDraftsProvider(_status).future);
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
            title: context.tr('cho.sectionTitle'),
            subtitle: context.tr('cho.sectionSub'),
          ),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              for (final (key, label) in <(String, String)>[
                ('review', context.tr('cho.filterAll')),
                (
                  'ready',
                  context.tr('cho.filterReady', <String, Object?>{
                    'count': summary?.ready ?? 0,
                  }),
                ),
                (
                  'needs_info',
                  context.tr('cho.filterNeedsInfo', <String, Object?>{
                    'count': summary?.needsInfo ?? 0,
                  }),
                ),
              ])
                FilterToggle(
                  key: ValueKey('chat-filter-$key'),
                  label: label,
                  selected: _status == key,
                  onChanged: (_) => setState(() => _status = key),
                ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          if (summary != null && !summary.anyChannel)
            _ConnectChatCard(onManage: _manageChannels),
          drafts.when(
            loading: () => const ContentLoader(minHeight: 90),
            error: (error, _) => error is ApiError
                ? ErrorStateCard(error: error, onRetry: _refreshChat)
                : const SizedBox.shrink(),
            data: (list) {
              final shown = list
                  .where((d) => provider == null || d.provider == provider)
                  .toList();
              if (shown.isEmpty) {
                return EmptyState(
                  icon: Icons.forum_outlined,
                  title: context.tr('cho.emptyTitle'),
                  message: context.tr('cho.emptyBody'),
                );
              }
              return Column(
                children: <Widget>[
                  for (final draft in shown)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 9),
                      child: ChatDraftCard(
                        draft: draft,
                        busy: _acting.contains(draft.id),
                        onReview: () => unawaited(_review(draft)),
                        onIgnore: () => unawaited(_ignore(draft)),
                      ),
                    ),
                ],
              );
            },
          ),
          if (attention != null && attention.isNotEmpty) ...<Widget>[
            SectionHeader(
              title: context.tr('cho.attentionTitle'),
              subtitle: context.tr('cho.attentionSub'),
            ),
            for (final item in attention)
              if (provider == null || item.provider == provider)
                Padding(
                  padding: const EdgeInsets.only(bottom: 9),
                  child: ChatAttentionTile(
                    item: item,
                    onOpenOrder: item.orderId == null
                        ? null
                        : () => unawaited(
                            Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) =>
                                    OrderDetailScreen(orderId: item.orderId!),
                              ),
                            ),
                          ),
                    onDone: () => unawaited(_resolve(item)),
                  ),
                ),
          ],
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

/// No Messenger or WhatsApp is connected, so no chat can become a draft.
class _ConnectChatCard extends StatelessWidget {
  const _ConnectChatCard({required this.onManage});

  final VoidCallback onManage;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      padding: const EdgeInsets.fromLTRB(13, 10, 6, 10),
      child: Row(
        children: <Widget>[
          const SoftIcon(
            icon: Icons.forum_outlined,
            tone: Tone.neutral,
            size: 36,
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              context.tr('cho.connectFirst'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          TextButton(
            key: const Key('chat-connect-channels'),
            onPressed: onManage,
            child: Text(context.tr('common.connect')),
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
