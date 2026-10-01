import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/money.dart';
import '../../data/chat_orders/chat_orders.dart';
import '../../data/commerce/list_controllers.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart' show formatRelative;
import 'inbox_screen.dart' show CustomerContextLine;

/// One draft order read from a customer's Messenger or WhatsApp messages,
/// for the seller to review. Never an order until they confirm it.
class ChatDraftCard extends ConsumerWidget {
  const ChatDraftCard({
    required this.draft,
    required this.onReview,
    required this.onIgnore,
    super.key,
    this.busy = false,
  });

  final ChatDraft draft;
  final VoidCallback onReview;
  final VoidCallback onIgnore;
  final bool busy;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final channel = context.tr('cho.provider.${draft.provider}');
    final missing = context.tr('inbox.notFound');
    final customerId = draft.customer?.id;
    final customer = customerId == null
        ? null
        : ref.watch(customerProvider(customerId)).valueOrNull;
    final quantity = draft.items.fold<int>(0, (sum, i) => sum + i.quantity);
    final product = draft.items.isEmpty
        ? missing
        : draft.items.length == 1
        ? draft.items.first.label
        : '${draft.items.first.label} +${draft.items.length - 1}';
    final phone =
        draft.selectedPhone ??
        (draft.phones.isEmpty
            ? missing
            : context.tr('cho.phonesToChoose', <String, Object?>{
                'count': draft.phones.length,
              }));
    final name =
        draft.customerName ?? draft.customer?.name ?? draft.senderName ?? '';
    final cod = draft.codAmountPaisa == null
        ? missing
        : draft.codSource == 'CATALOG'
        ? context.tr('cho.codEstimate', <String, Object?>{
            'amount': Money(draft.codAmountPaisa!).format(),
          })
        : Money(draft.codAmountPaisa!).format();
    final productUnsure =
        draft.items.isEmpty ||
        draft.items.any(
          (item) => !item.match.isMatched || item.match.needsChoice,
        );

    final fields = <(String, String, bool)>[
      (
        context.tr('inbox.f.name'),
        name.isEmpty ? missing : name,
        draft.isUncertain('name'),
      ),
      (context.tr('inbox.f.product'), product, productUnsure),
      (
        context.tr('inbox.f.quantity'),
        quantity == 0 ? missing : '$quantity',
        quantity == 0,
      ),
      (
        context.tr('inbox.f.phone'),
        phone,
        draft.isMissing('phone') || draft.isUncertain('phone'),
      ),
      (
        context.tr('inbox.f.address'),
        draft.address ?? missing,
        draft.isMissing('address') || draft.isUncertain('address'),
      ),
      (
        context.tr('inbox.f.cod'),
        cod,
        draft.codAmountPaisa == null || draft.isUncertain('amount'),
      ),
    ];

    final notes = <String>[
      if (draft.missingFields.isNotEmpty)
        context.tr('cho.missing', <String, Object?>{
          'fields': draft.missingFields
              .map((field) => context.tr('cho.field.$field'))
              .join(', '),
        }),
      for (final warning in draft.warnings)
        if (warning != 'ATTACHMENT') context.tr('cho.warn.$warning'),
      if (draft.attachmentCount > 0)
        context.tr('cho.attachment', <String, Object?>{'channel': channel}),
    ];

    return GlassCard(
      key: ValueKey('chat-draft-${draft.id}'),
      padding: const EdgeInsets.all(14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                draft.provider == 'WHATSAPP'
                    ? Icons.chat_rounded
                    : Icons.facebook_rounded,
                size: 18,
                color: EcomsbdColors.blue,
              ),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  context.tr('cho.newFrom', <String, Object?>{
                    'channel': channel,
                  }),
                  style: EcomsbdType.bodyStrong.copyWith(fontSize: 13.5),
                ),
              ),
              StatusChip(
                label: draft.isReady
                    ? context.tr('cho.ready')
                    : context.tr('cho.needsInfo'),
                tone: draft.isReady ? Tone.info : Tone.warning,
                showIcon: false,
              ),
            ],
          ),
          Text(
            [
              if (draft.channelName != null) draft.channelName!,
              formatRelative(draft.lastMessageAt),
              context.tr('cho.messages', <String, Object?>{
                'count': draft.messageCount,
              }),
            ].join(' · '),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: 8),
          for (final (label, value, unsure) in fields)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  SizedBox(
                    width: 92,
                    child: Text(
                      label,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ),
                  Expanded(
                    child: Text(
                      value,
                      style: EcomsbdType.body.copyWith(
                        fontWeight: FontWeight.w600,
                        color: unsure ? EcomsbdColors.amber : null,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          for (final note in notes)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(
                note,
                style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
              ),
            ),
          if (customer != null) ...<Widget>[
            const SizedBox(height: 8),
            CustomerContextLine(customer: customer),
          ] else if (draft.customerMatch == 'AMBIGUOUS') ...<Widget>[
            const SizedBox(height: 6),
            Text(
              context.tr('cho.customerAmbiguous'),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.amber),
            ),
          ],
          const SizedBox(height: 10),
          Row(
            children: <Widget>[
              Expanded(
                child: FilledButton(
                  key: ValueKey('chat-review-${draft.id}'),
                  onPressed: busy ? null : onReview,
                  style: FilledButton.styleFrom(
                    backgroundColor: EcomsbdColors.orange,
                    minimumSize: const Size.fromHeight(44),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  child: Text(context.tr('cho.review')),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                key: ValueKey('chat-ignore-${draft.id}'),
                onPressed: busy ? null : onIgnore,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size(0, 44),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14),
                  ),
                ),
                child: Text(context.tr('cho.ignore')),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

/// A later message that may be about an order already placed. A prompt only:
/// nothing is cancelled or changed because a customer wrote it.
class ChatAttentionTile extends StatelessWidget {
  const ChatAttentionTile({
    required this.item,
    required this.onOpenOrder,
    required this.onDone,
    super.key,
  });

  final ChatAttentionItem item;
  final VoidCallback? onOpenOrder;
  final VoidCallback onDone;

  @override
  Widget build(BuildContext context) {
    final channel = context.tr('cho.provider.${item.provider}');
    return GlassCard(
      key: ValueKey('chat-attention-${item.id}'),
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Text(
            context.tr('cho.intent.${item.intent}'),
            style: EcomsbdType.bodyStrong,
          ),
          Text(
            [
              if (item.customerName != null) item.customerName!,
              channel,
              formatRelative(item.messageAt),
            ].join(' · '),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (item.excerpt != null) ...<Widget>[
            const SizedBox(height: 4),
            Text('“${item.excerpt}”', style: EcomsbdType.body),
          ],
          const SizedBox(height: 4),
          Text(
            context.tr('cho.attentionNote'),
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
          Wrap(
            spacing: 8,
            children: <Widget>[
              if (onOpenOrder != null)
                TextButton(
                  onPressed: onOpenOrder,
                  child: Text(context.tr('inbox.openOrder')),
                ),
              TextButton(
                key: ValueKey('chat-attention-done-${item.id}'),
                onPressed: onDone,
                child: Text(context.tr('cho.done')),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
