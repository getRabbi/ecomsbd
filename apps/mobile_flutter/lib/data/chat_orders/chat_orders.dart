import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../app/providers.dart';
import '../../core/api/api_client.dart';
import '../commerce/models.dart';

/// Order automation from Messenger and WhatsApp (`/v1/chat-orders`).
///
/// The server reads a customer's burst of chat messages into one draft with
/// the same parser as "paste a message". Nothing here is an order: a draft
/// becomes one only when the seller reviews and confirms it, and confirming
/// goes through the server's normal order creation. There is no chat client
/// here: the conversation stays in Messenger or WhatsApp.

/// MESSENGER or WHATSAPP.
typedef ChatProvider = String;

@immutable
class ChatDraftMatch {
  const ChatDraftMatch({
    required this.status,
    this.productId,
    this.productName,
    this.variantStatus = 'NONE',
    this.variantId,
    this.variantName,
    this.unitPricePaisa,
    this.candidates = const <({String id, String name})>[],
    this.variantCandidates = const <({String id, String name})>[],
  });

  factory ChatDraftMatch.fromJson(Map<String, dynamic>? json) {
    if (json == null) return const ChatDraftMatch(status: 'NOT_FOUND');
    List<({String id, String name})> list(String key, String idKey) =>
        <({String id, String name})>[
          for (final row in (json[key] as List<dynamic>? ?? const <dynamic>[]))
            if (row is Map<String, dynamic>)
              (id: '${row[idKey]}', name: '${row['name']}'),
        ];
    return ChatDraftMatch(
      status: json['status'] as String? ?? 'NOT_FOUND',
      productId: json['product_id'] as String?,
      productName: json['product_name'] as String?,
      variantStatus: json['variant_status'] as String? ?? 'NONE',
      variantId: json['variant_id'] as String?,
      variantName: json['variant_name'] as String?,
      unitPricePaisa: json['unit_price_paisa'] as int?,
      candidates: list('candidates', 'product_id'),
      variantCandidates: list('variant_candidates', 'variant_id'),
    );
  }

  /// MATCHED, AMBIGUOUS or NOT_FOUND.
  final String status;
  final String? productId;
  final String? productName;

  /// MATCHED, AMBIGUOUS or NONE (the product has no variants).
  final String variantStatus;
  final String? variantId;
  final String? variantName;
  final int? unitPricePaisa;
  final List<({String id, String name})> candidates;
  final List<({String id, String name})> variantCandidates;

  bool get isMatched => status == 'MATCHED';
  bool get needsChoice =>
      status == 'AMBIGUOUS' || (isMatched && variantStatus == 'AMBIGUOUS');
}

@immutable
class ChatDraftItem {
  const ChatDraftItem({
    required this.name,
    required this.quantity,
    required this.match,
    this.size,
    this.color,
  });

  factory ChatDraftItem.fromJson(Map<String, dynamic> json) => ChatDraftItem(
    name: json['name'] as String? ?? '',
    quantity: json['quantity'] as int? ?? 1,
    size: json['size'] as String?,
    color: json['color'] as String?,
    match: ChatDraftMatch.fromJson(json['match'] as Map<String, dynamic>?),
  );

  /// What the customer wrote, without the request words.
  final String name;
  final int quantity;
  final String? size;
  final String? color;
  final ChatDraftMatch match;

  /// The catalogue product (and variant) when matched, else the words.
  String get label {
    if (match.isMatched && match.productName != null) {
      return match.variantName == null
          ? match.productName!
          : '${match.productName} / ${match.variantName}';
    }
    return <String>[name, if (size != null) size!].join(' / ');
  }
}

@immutable
class ChatDraftCustomer {
  const ChatDraftCustomer({
    required this.id,
    this.name,
    this.phoneMasked,
    this.orderCount = 0,
    this.isBlocked = false,
  });

  factory ChatDraftCustomer.fromJson(Map<String, dynamic> json) =>
      ChatDraftCustomer(
        id: '${json['id']}',
        name: json['name'] as String?,
        phoneMasked: json['phone_masked'] as String?,
        orderCount: json['order_count'] as int? ?? 0,
        isBlocked: json['is_blocked'] as bool? ?? false,
      );

  final String id;
  final String? name;
  final String? phoneMasked;
  final int orderCount;
  final bool isBlocked;
}

@immutable
class ChatDraft {
  const ChatDraft({
    required this.id,
    required this.provider,
    required this.status,
    required this.items,
    required this.phones,
    required this.missingFields,
    required this.uncertainFields,
    required this.warnings,
    required this.lastMessageAt,
    this.channelName,
    this.senderName,
    this.senderPhoneMasked,
    this.customerMatch = 'NONE',
    this.customer,
    this.customerName,
    this.selectedPhone,
    this.address,
    this.codAmountPaisa,
    this.codSource,
    this.notes,
    this.messageCount = 0,
    this.attachmentCount = 0,
    this.confirmedOrderId,
  });

  factory ChatDraft.fromJson(Map<String, dynamic> json) {
    List<String> strings(String key) => <String>[
      for (final value in (json[key] as List<dynamic>? ?? const <dynamic>[]))
        '$value',
    ];
    final sender = json['sender'] as Map<String, dynamic>? ?? const {};
    final customer = json['customer'];
    return ChatDraft(
      id: '${json['id']}',
      provider: json['provider'] as String? ?? 'MESSENGER',
      status: json['status'] as String? ?? 'COLLECTING',
      channelName: json['channel_name'] as String?,
      senderName: sender['display_name'] as String?,
      senderPhoneMasked: sender['phone_masked'] as String?,
      customerMatch: json['customer_match'] as String? ?? 'NONE',
      customer: customer is Map<String, dynamic>
          ? ChatDraftCustomer.fromJson(customer)
          : null,
      customerName: json['customer_name'] as String?,
      phones: strings('phones'),
      selectedPhone: json['selected_phone'] as String?,
      address: json['address'] as String?,
      items: <ChatDraftItem>[
        for (final item in (json['items'] as List<dynamic>? ?? const []))
          if (item is Map<String, dynamic>) ChatDraftItem.fromJson(item),
      ],
      codAmountPaisa: json['cod_amount_paisa'] as int?,
      codSource: json['cod_source'] as String?,
      notes: json['notes'] as String?,
      warnings: strings('warnings'),
      uncertainFields: strings('uncertain_fields'),
      missingFields: strings('missing_fields'),
      messageCount: json['message_count'] as int? ?? 0,
      attachmentCount: json['attachment_count'] as int? ?? 0,
      lastMessageAt:
          DateTime.tryParse('${json['last_message_at']}')?.toUtc() ??
          DateTime.now().toUtc(),
      confirmedOrderId: json['confirmed_order_id'] as String?,
    );
  }

  final String id;
  final ChatProvider provider;

  /// COLLECTING, NEEDS_INFO, READY_FOR_REVIEW, CONFIRMED, IGNORED, EXPIRED.
  final String status;
  final String? channelName;
  final String? senderName;
  final String? senderPhoneMasked;

  /// NONE, MATCHED, SUGGESTED or AMBIGUOUS.
  final String customerMatch;
  final ChatDraftCustomer? customer;
  final String? customerName;
  final List<String> phones;
  final String? selectedPhone;
  final String? address;
  final List<ChatDraftItem> items;
  final int? codAmountPaisa;

  /// PARSED (the customer wrote it) or CATALOG (price x quantity).
  final String? codSource;
  final String? notes;
  final List<String> warnings;
  final List<String> uncertainFields;
  final List<String> missingFields;
  final int messageCount;
  final int attachmentCount;
  final DateTime lastMessageAt;
  final String? confirmedOrderId;

  bool get isReady => status == 'READY_FOR_REVIEW';
  bool get isOpen =>
      status == 'READY_FOR_REVIEW' ||
      status == 'NEEDS_INFO' ||
      status == 'COLLECTING';
  bool isUncertain(String field) => uncertainFields.contains(field);
  bool isMissing(String field) => missingFields.contains(field);

  /// The draft as the review form's starting point. Product matches travel
  /// separately, in [items].
  ParsedOrder toParsed() => ParsedOrder(
    sourceText: '',
    customerName: customerName,
    phones: phones,
    selectedPhone: selectedPhone,
    address: address,
    items: <ParsedItem>[
      for (final item in items)
        ParsedItem(
          name: item.match.isMatched && item.match.productName != null
              ? item.match.productName!
              : item.name,
          quantity: item.quantity,
          size: item.match.isMatched ? null : item.size,
          color: item.match.isMatched ? null : item.color,
          unitPricePaisa: item.match.unitPricePaisa,
        ),
    ],
    codAmountPaisa: codAmountPaisa,
    notes: notes,
    confidence: <String, double>{
      for (final field in <String>['name', 'phone', 'address', 'amount'])
        field: isUncertain(field) || isMissing(field) ? 0.25 : 1.0,
    },
    warnings: const <String>[],
    needsPhoneSelection: phones.length > 1 && selectedPhone == null,
    isLowConfidence: false,
  );
}

@immutable
class ChatAttentionItem {
  const ChatAttentionItem({
    required this.id,
    required this.provider,
    required this.intent,
    required this.messageAt,
    this.orderId,
    this.customerId,
    this.customerName,
    this.excerpt,
  });

  factory ChatAttentionItem.fromJson(Map<String, dynamic> json) {
    final customer = json['customer'] as Map<String, dynamic>?;
    return ChatAttentionItem(
      id: '${json['id']}',
      provider: json['provider'] as String? ?? 'MESSENGER',
      intent: json['intent'] as String? ?? 'STATUS_QUESTION',
      orderId: json['order_id'] as String?,
      customerId: customer?['id'] as String?,
      customerName: customer?['name'] as String?,
      excerpt: json['excerpt'] as String?,
      messageAt:
          DateTime.tryParse('${json['message_at']}')?.toUtc() ??
          DateTime.now().toUtc(),
    );
  }

  final String id;
  final ChatProvider provider;

  /// CANCEL_REQUEST, ADDRESS_CHANGE or STATUS_QUESTION.
  final String intent;
  final String? orderId;
  final String? customerId;
  final String? customerName;
  final String? excerpt;
  final DateTime messageAt;
}

@immutable
class ChatOrderSummary {
  const ChatOrderSummary({
    required this.ready,
    required this.needsInfo,
    required this.attention,
    required this.messengerConnected,
    required this.whatsappConnected,
  });

  factory ChatOrderSummary.fromJson(Map<String, dynamic> json) {
    final channels = json['channels'] as Map<String, dynamic>? ?? const {};
    return ChatOrderSummary(
      ready: json['ready'] as int? ?? 0,
      needsInfo: json['needs_info'] as int? ?? 0,
      attention: json['attention'] as int? ?? 0,
      messengerConnected: channels['MESSENGER'] == true,
      whatsappConnected: channels['WHATSAPP'] == true,
    );
  }

  final int ready;
  final int needsInfo;
  final int attention;
  final bool messengerConnected;
  final bool whatsappConnected;

  bool get anyChannel => messengerConnected || whatsappConnected;
}

/// The result of confirming a draft: the order the server created (or had
/// already created for this draft).
@immutable
class ChatConfirmResult {
  const ChatConfirmResult({
    required this.order,
    required this.replayed,
    this.duplicateCheck,
  });

  final SellerOrder order;
  final bool replayed;
  final DuplicateCheck? duplicateCheck;
}

class ChatOrdersRepository {
  ChatOrdersRepository(this._api);

  final ApiClient _api;

  Future<ChatOrderSummary> summary() async =>
      ChatOrderSummary.fromJson(await _api.get('/chat-orders/summary'));

  Future<List<ChatDraft>> drafts({
    String status = 'review',
    ChatProvider? provider,
  }) async {
    final json = await _api.get(
      '/chat-orders',
      query: <String, dynamic>{
        'status': status,
        if (provider != null) 'provider': provider,
      },
    );
    return <ChatDraft>[
      for (final row in (json['items'] as List<dynamic>? ?? const []))
        if (row is Map<String, dynamic>) ChatDraft.fromJson(row),
    ];
  }

  Future<ChatDraft> draft(String id) async =>
      ChatDraft.fromJson(await _api.get('/chat-orders/$id'));

  Future<ChatDraft> ignore(String id) async =>
      ChatDraft.fromJson(await _api.post('/chat-orders/$id/ignore'));

  /// Confirm the reviewed order. Safe to repeat: the server returns the order
  /// it already made for this draft.
  Future<ChatConfirmResult> confirm(
    String id,
    Map<String, dynamic> order,
  ) async {
    final json = await _api.post('/chat-orders/$id/confirm', body: order);
    final duplicates = json['duplicate_check'];
    return ChatConfirmResult(
      order: SellerOrder.fromJson(json['order'] as Map<String, dynamic>),
      replayed: json['replayed'] == true,
      duplicateCheck: duplicates is Map<String, dynamic>
          ? DuplicateCheck.fromJson(duplicates)
          : null,
    );
  }

  Future<List<ChatAttentionItem>> attention() async {
    final json = await _api.get('/chat-orders/attention');
    return <ChatAttentionItem>[
      for (final row in (json['items'] as List<dynamic>? ?? const []))
        if (row is Map<String, dynamic>) ChatAttentionItem.fromJson(row),
    ];
  }

  Future<void> resolveAttention(String id) async {
    await _api.post('/chat-orders/attention/$id/resolve');
  }
}

final chatOrdersRepositoryProvider = Provider<ChatOrdersRepository>(
  (ref) => ChatOrdersRepository(ref.watch(apiClientProvider)),
);

final chatOrderSummaryProvider = FutureProvider.autoDispose<ChatOrderSummary>(
  (ref) => ref.watch(chatOrdersRepositoryProvider).summary(),
);

/// Drafts for the Inbox, by review filter (`review`, `ready`, `needs_info`).
final chatDraftsProvider = FutureProvider.autoDispose
    .family<List<ChatDraft>, String>(
      (ref, status) =>
          ref.watch(chatOrdersRepositoryProvider).drafts(status: status),
    );

final chatAttentionProvider =
    FutureProvider.autoDispose<List<ChatAttentionItem>>(
      (ref) => ref.watch(chatOrdersRepositoryProvider).attention(),
    );
