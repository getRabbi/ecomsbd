import 'package:meta/meta.dart';

import '../../core/money.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Seller-facing labels below are read where no `BuildContext` exists, so
/// they resolve against the active locale directly — the same approach
/// `formatRelative` uses. A language change rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key, [Map<String, Object?>? vars]) =>
    AppStrings(activeAppLocale).t(key, vars);

/// Wire models for the commerce core.
///
/// Money arrives as integer paisa and stays that way (master spec section 32).
/// Phones arrive masked; the client never receives a full number except from
/// the explicit reveal endpoint (section 133).

// --------------------------------------------------------------------------- //
// Products
// --------------------------------------------------------------------------- //

@immutable
class Product {
  const Product({
    required this.id,
    required this.name,
    required this.cost,
    required this.sellingPrice,
    required this.stockOnHand,
    required this.stockTrackingEnabled,
    required this.isActive,
    required this.isArchived,
    required this.isLowStock,
    this.sku,
    this.description,
    this.lowStockThreshold,
    this.hasVariants = false,
    this.variants = const <ProductVariant>[],
  });

  factory Product.fromJson(Map<String, dynamic> json) => Product(
    id: json['id'] as String,
    name: json['name'] as String,
    sku: json['sku'] as String?,
    description: json['description'] as String?,
    cost: Money(json['cost_paisa'] as int),
    sellingPrice: Money(json['default_selling_price_paisa'] as int),
    stockOnHand: json['stock_on_hand'] as int,
    stockTrackingEnabled: json['stock_tracking_enabled'] as bool? ?? true,
    lowStockThreshold: json['low_stock_threshold'] as int?,
    isLowStock: json['is_low_stock'] as bool? ?? false,
    isActive: json['is_active'] as bool? ?? true,
    isArchived: json['is_archived'] as bool? ?? false,
    hasVariants: json['has_variants'] as bool? ?? false,
    variants: <ProductVariant>[
      for (final item
          in (json['variants'] as List<dynamic>? ?? const <dynamic>[]))
        ProductVariant.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String id;
  final String name;
  final String? sku;
  final String? description;
  final Money cost;
  final Money sellingPrice;
  final int stockOnHand;
  final bool stockTrackingEnabled;
  final int? lowStockThreshold;
  final bool isLowStock;
  final bool isActive;
  final bool isArchived;

  /// Stock is held per variant; [stockOnHand] is then their sum.
  final bool hasVariants;
  final List<ProductVariant> variants;

  List<ProductVariant> get activeVariants =>
      variants.where((variant) => variant.isActive).toList();

  /// Indicative unit margin. Not profit — that needs a settled delivery.
  Money get margin => sellingPrice - cost;
}

/// One sellable version of a product — "Black / M" — with its own stock.
@immutable
class ProductVariant {
  const ProductVariant({
    required this.id,
    required this.name,
    required this.stockOnHand,
    required this.isLowStock,
    required this.isActive,
    this.sku,
    this.lowStockThreshold,
  });

  factory ProductVariant.fromJson(Map<String, dynamic> json) => ProductVariant(
    id: json['id'] as String,
    name: json['name'] as String,
    sku: json['sku'] as String?,
    stockOnHand: json['stock_on_hand'] as int? ?? 0,
    lowStockThreshold: json['low_stock_threshold'] as int?,
    isLowStock: json['is_low_stock'] as bool? ?? false,
    isActive: json['is_active'] as bool? ?? true,
  );

  final String id;
  final String name;
  final String? sku;
  final int stockOnHand;
  final int? lowStockThreshold;
  final bool isLowStock;
  final bool isActive;
}

@immutable
class StockMovement {
  const StockMovement({
    required this.id,
    required this.quantityDelta,
    required this.balanceAfter,
    required this.reason,
    required this.source,
    required this.occurredAt,
    this.note,
    this.variantName,
    this.orderNumber,
    this.reference,
    this.actorName,
  });

  factory StockMovement.fromJson(Map<String, dynamic> json) => StockMovement(
    id: json['id'] as String,
    quantityDelta: json['quantity_delta'] as int,
    balanceAfter: json['balance_after'] as int,
    reason: json['reason'] as String,
    source: json['source'] as String,
    note: json['note'] as String?,
    occurredAt: DateTime.parse(json['occurred_at'] as String),
    variantName: json['variant_name'] as String?,
    orderNumber: json['order_number'] as String?,
    reference: json['reference'] as String?,
    actorName: json['actor_name'] as String?,
  );

  final String id;
  final int quantityDelta;
  final int balanceAfter;
  final String reason;
  final String source;
  final String? note;
  final DateTime occurredAt;
  final String? variantName;
  final String? orderNumber;
  final String? reference;
  final String? actorName;

  bool get isIncrease => quantityDelta > 0;

  /// Seller-facing wording for a reason code.
  String get reasonLabel => switch (reason) {
    'OPENING' => _t('mv.opening'),
    'MANUAL_ADJUSTMENT' => _t('mv.manual'),
    'BOOKED_RESERVE' => _t('mv.reserved'),
    'BOOKED_DECREMENT' => _t('mv.bookedCourier'),
    'CANCEL_RESTORE' => _t('mv.orderCancelled'),
    'RETURN_RESTORE' => _t('mv.returnedToStock'),
    'PARTIAL_RETURN_RESTORE' => _t('mv.partialReturn'),
    'DAMAGED_WRITE_OFF' => _t('mv.damagedWriteOff'),
    'IMPORT_ADJUSTMENT' => _t('mv.fromImport'),
    'RESTOCK' => _t('mv.restock'),
    'EXTERNAL_SYNC' => _t('mv.externalSync'),
    'TRANSFER_OUT' => _t('mv.transferOut'),
    'TRANSFER_IN' => _t('mv.transferIn'),
    _ => reason,
  };
}

// --------------------------------------------------------------------------- //
// Customers
// --------------------------------------------------------------------------- //

@immutable
class Customer {
  const Customer({
    required this.id,
    required this.phoneMasked,
    required this.phoneLast4,
    required this.flag,
    required this.orderCount,
    required this.deliveredCount,
    required this.returnedCount,
    required this.cancelledCount,
    required this.realizedRevenue,
    required this.isRepeatBuyer,
    this.name,
    this.notes,
    this.flagReason,
    this.successRateBasisPoints,
    this.firstOrderAt,
    this.lastOrderAt,
    this.addresses = const <CustomerAddress>[],
    this.crmTags = const <String>[],
    this.crmSegments = const <String>[],
    this.historicalRevenueAvailable = false,
  });

  factory Customer.fromJson(Map<String, dynamic> json) => Customer(
    id: json['id'] as String,
    name: json['name'] as String?,
    phoneMasked: json['phone_masked'] as String,
    phoneLast4: json['phone_last4'] as String,
    flag: json['flag'] as String? ?? 'NONE',
    flagReason: json['flag_reason'] as String?,
    notes: json['notes'] as String?,
    orderCount: json['order_count'] as int? ?? 0,
    deliveredCount: json['delivered_count'] as int? ?? 0,
    returnedCount: json['returned_count'] as int? ?? 0,
    cancelledCount: json['cancelled_count'] as int? ?? 0,
    successRateBasisPoints: json['success_rate_basis_points'] as int?,
    realizedRevenue: Money(json['realized_revenue_paisa'] as int? ?? 0),
    historicalRevenueAvailable:
        json['realized_revenue_paisa'] != null && json['value'] != null,
    crmTags: [
      for (final tag in json['tags'] as List? ?? [])
        (tag as Map)['name'] as String,
    ],
    crmSegments: (json['segments'] as List? ?? []).cast<String>(),
    isRepeatBuyer: json['is_repeat_buyer'] as bool? ?? false,
    firstOrderAt: json['first_order_at'] == null
        ? null
        : DateTime.parse(json['first_order_at'] as String),
    lastOrderAt: json['last_order_at'] == null
        ? null
        : DateTime.parse(json['last_order_at'] as String),
    addresses: <CustomerAddress>[
      for (final item
          in (json['addresses'] as List<dynamic>? ?? const <dynamic>[]))
        CustomerAddress.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String id;
  final String? name;

  /// `01712****78`. The only phone form the client normally holds.
  final String phoneMasked;
  final String phoneLast4;

  final String flag;
  final String? flagReason;
  final String? notes;

  final int orderCount;
  final int deliveredCount;
  final int returnedCount;
  final int cancelledCount;

  /// `null` when there is no terminal history yet. Rendered as "No history",
  /// never as 0% — that would read as a judgement the data cannot support.
  final int? successRateBasisPoints;

  final Money realizedRevenue;
  final bool isRepeatBuyer;
  final DateTime? firstOrderAt;
  final DateTime? lastOrderAt;
  final List<CustomerAddress> addresses;
  final List<String> crmTags;
  final List<String> crmSegments;
  final bool historicalRevenueAvailable;

  bool get isBlocked => flag == 'BLOCKED';
  bool get isStarred => flag == 'STARRED';

  String get displayName =>
      name?.trim().isNotEmpty == true ? name! : phoneMasked;

  String get successRateLabel {
    final rate = successRateBasisPoints;
    if (rate == null) {
      return _t('cust.noHistory');
    }
    return _t('cust.deliveredPct', <String, Object?>{
      'pct': (rate / 100).toStringAsFixed(1),
    });
  }

  CustomerAddress? get defaultAddress {
    for (final address in addresses) {
      if (address.isDefault) {
        return address;
      }
    }
    return addresses.isEmpty ? null : addresses.first;
  }
}

@immutable
class CustomerAddress {
  const CustomerAddress({
    required this.id,
    required this.rawAddress,
    required this.isDefault,
    this.normalizedAddress,
    this.label,
    this.district,
    this.area,
  });

  factory CustomerAddress.fromJson(Map<String, dynamic> json) =>
      CustomerAddress(
        id: json['id'] as String,
        rawAddress: json['raw_address'] as String,
        normalizedAddress: json['normalized_address'] as String?,
        label: json['label'] as String?,
        district: json['district'] as String?,
        area: json['area'] as String?,
        isDefault: json['is_default'] as bool? ?? false,
      );

  final String id;

  /// Exactly what the seller typed. Never replaced by a courier's version.
  final String rawAddress;
  final String? normalizedAddress;
  final String? label;
  final String? district;
  final String? area;
  final bool isDefault;
}

// --------------------------------------------------------------------------- //
// Orders
// --------------------------------------------------------------------------- //

@immutable
class OrderItem {
  const OrderItem({
    required this.id,
    required this.productName,
    required this.quantity,
    required this.unitPrice,
    required this.unitCostSnapshot,
    required this.lineTotal,
    this.productId,
    this.sku,
    this.variantLabel,
    this.note,
  });

  factory OrderItem.fromJson(Map<String, dynamic> json) => OrderItem(
    id: json['id'] as String,
    productId: json['product_id'] as String?,
    productName: json['product_name'] as String,
    sku: json['sku'] as String?,
    variantLabel: json['variant_label'] as String?,
    quantity: json['quantity'] as int,
    unitPrice: Money(json['unit_price_paisa'] as int),
    unitCostSnapshot: Money(json['unit_cost_snapshot_paisa'] as int),
    lineTotal: Money(json['line_total_paisa'] as int),
    note: json['note'] as String?,
  );

  final String id;
  final String? productId;
  final String productName;
  final String? sku;
  final String? variantLabel;
  final int quantity;
  final Money unitPrice;

  /// The product's cost when the order was placed. Editing the product later
  /// does not move this (master spec section 18.2).
  final Money unitCostSnapshot;

  final Money lineTotal;
  final String? note;
}

@immutable
class SellerOrder {
  const SellerOrder({
    required this.id,
    required this.orderNumber,
    required this.clientId,
    required this.status,
    required this.channel,
    required this.businessDate,
    required this.subtotal,
    required this.discount,
    required this.deliveryFee,
    required this.codAmount,
    required this.version,
    required this.createdAt,
    required this.fulfillmentState,
    required this.riskState,
    required this.profitState,
    this.customerId,
    this.customerName,
    this.customerPhoneMasked,
    this.deliveryAddress,
    this.deliveryDistrict,
    this.deliveryArea,
    this.note,
    this.sourceText,
    this.items = const <OrderItem>[],
    this.consignmentId,
    this.consignmentStatus,
    this.returnPendingUnits = 0,
  });

  factory SellerOrder.fromJson(Map<String, dynamic> json) => SellerOrder(
    id: json['id'] as String,
    orderNumber: json['order_number'] as String,
    clientId: json['client_id'] as String,
    customerId: json['customer_id'] as String?,
    customerName: json['customer_name'] as String?,
    customerPhoneMasked: json['customer_phone_masked'] as String?,
    deliveryAddress: json['delivery_address_raw'] as String?,
    deliveryDistrict: json['delivery_district'] as String?,
    deliveryArea: json['delivery_area'] as String?,
    status: json['status'] as String,
    channel: json['channel'] as String,
    businessDate: DateTime.parse(json['business_date'] as String),
    subtotal: Money(json['subtotal_paisa'] as int),
    discount: Money(json['discount_paisa'] as int),
    deliveryFee: Money(json['delivery_fee_paisa'] as int),
    codAmount: Money(json['cod_amount_paisa'] as int),
    note: json['note'] as String?,
    sourceText: json['source_text'] as String?,
    version: json['version'] as int? ?? 1,
    createdAt: DateTime.parse(json['created_at'] as String),
    fulfillmentState: json['fulfillment_state'] as String? ?? 'NOT_BOOKED',
    riskState: json['risk_state'] as String? ?? 'NOT_CHECKED',
    profitState: json['profit_state'] as String? ?? 'PENDING_CALCULATION',
    items: <OrderItem>[
      for (final item in (json['items'] as List<dynamic>? ?? const <dynamic>[]))
        OrderItem.fromJson(item as Map<String, dynamic>),
    ],
    consignmentId: json['consignment_id'] as String?,
    consignmentStatus: json['consignment_status'] as String?,
    returnPendingUnits: json['return_pending_units'] as int? ?? 0,
  );

  final String id;
  final String orderNumber;
  final String clientId;
  final String? customerId;
  final String? customerName;
  final String? customerPhoneMasked;
  final String? deliveryAddress;
  final String? deliveryDistrict;
  final String? deliveryArea;
  final String status;
  final String channel;
  final DateTime businessDate;
  final Money subtotal;
  final Money discount;
  final Money deliveryFee;
  final Money codAmount;
  final String? note;
  final String? sourceText;
  final int version;
  final DateTime createdAt;
  final List<OrderItem> items;

  /// The order's newest parcel (detail reads only).
  final String? consignmentId;
  final String? consignmentStatus;

  /// Returned units nobody has restocked or written off yet. A courier saying
  /// "returned" never puts stock back by itself; the seller does.
  final int returnPendingUnits;

  /// Placeholders until the engines that produce them exist. The server sends
  /// these explicitly so the UI shows "Not booked" / "Not checked" / "Pending"
  /// rather than a fabricated courier state, risk band or profit figure.
  final String fulfillmentState;
  final String riskState;
  final String profitState;

  String get statusLabel => switch (status) {
    'DRAFT' => _t('ost.draft'),
    'CONFIRMED' => _t('ost.confirmed'),
    'PACKED' => _t('ost.packed'),
    'FULFILLMENT_STARTED' => _t('ost.withCourier'),
    'COMPLETED' => _t('ost.completed'),
    'CANCELLED' => _t('ost.cancelled'),
    _ => status,
  };

  String get itemSummary {
    if (items.isEmpty) {
      return '';
    }
    final first = items.first.productName;
    return items.length == 1 ? first : '$first +${items.length - 1}';
  }

  bool get isEditable =>
      status == 'DRAFT' || status == 'CONFIRMED' || status == 'PACKED';
}

@immutable
class DuplicateCandidate {
  const DuplicateCandidate({
    required this.orderId,
    required this.orderNumber,
    required this.status,
    required this.codAmount,
    required this.hoursAgo,
    required this.reasons,
    required this.isStrong,
  });

  factory DuplicateCandidate.fromJson(Map<String, dynamic> json) =>
      DuplicateCandidate(
        orderId: json['order_id'] as String,
        orderNumber: json['order_number'] as String,
        status: json['status'] as String,
        codAmount: Money(json['cod_amount_paisa'] as int),
        hoursAgo: (json['hours_ago'] as num).toDouble(),
        reasons: <String>[
          for (final reason
              in (json['reasons'] as List<dynamic>? ?? const <dynamic>[]))
            reason as String,
        ],
        isStrong: json['is_strong'] as bool? ?? false,
      );

  final String orderId;
  final String orderNumber;
  final String status;
  final Money codAmount;
  final double hoursAgo;
  final List<String> reasons;
  final bool isStrong;

  String get whenLabel {
    if (hoursAgo < 1) {
      return '${(hoursAgo * 60).round()} minutes ago';
    }
    if (hoursAgo < 24) {
      return '${hoursAgo.round()} hours ago';
    }
    return '${(hoursAgo / 24).round()} days ago';
  }
}

/// A warning, never a block (master spec section 9).
@immutable
class DuplicateCheck {
  const DuplicateCheck({
    required this.possibleDuplicate,
    this.message = '',
    this.candidates = const <DuplicateCandidate>[],
  });

  factory DuplicateCheck.fromJson(Map<String, dynamic> json) => DuplicateCheck(
    possibleDuplicate: json['possible_duplicate'] as bool? ?? false,
    message: json['message'] as String? ?? '',
    candidates: <DuplicateCandidate>[
      for (final item
          in (json['candidates'] as List<dynamic>? ?? const <dynamic>[]))
        DuplicateCandidate.fromJson(item as Map<String, dynamic>),
    ],
  );

  final bool possibleDuplicate;
  final String message;
  final List<DuplicateCandidate> candidates;
}

@immutable
class OrderCreateResult {
  const OrderCreateResult({required this.order, this.duplicateCheck});

  factory OrderCreateResult.fromJson(Map<String, dynamic> json) =>
      OrderCreateResult(
        order: SellerOrder.fromJson(json['order'] as Map<String, dynamic>),
        duplicateCheck: json['duplicate_check'] == null
            ? null
            : DuplicateCheck.fromJson(
                json['duplicate_check'] as Map<String, dynamic>,
              ),
      );

  final SellerOrder order;
  final DuplicateCheck? duplicateCheck;
}

// --------------------------------------------------------------------------- //
// Parser
// --------------------------------------------------------------------------- //

@immutable
class ParsedItem {
  const ParsedItem({
    required this.name,
    required this.quantity,
    this.size,
    this.color,
    this.unitPricePaisa,
  });

  factory ParsedItem.fromJson(Map<String, dynamic> json) => ParsedItem(
    name: json['name'] as String,
    quantity: json['quantity'] as int? ?? 1,
    size: json['size'] as String?,
    color: json['color'] as String?,
    unitPricePaisa: json['unit_price_paisa'] as int?,
  );

  final String name;
  final int quantity;
  final String? size;
  final String? color;
  final int? unitPricePaisa;

  String get displayName {
    // The parser keeps a colour in the name ("কালো পাঞ্জাবি"); say it once.
    final named = name.toLowerCase();
    final parts = <String>[
      name,
      if (color != null && !named.contains(color!.toLowerCase())) color!,
      if (size != null) size!,
    ];
    return parts.join(' ');
  }
}

/// Result of `POST /v1/orders/parse`.
///
/// Nothing here has been saved. The seller confirms or edits every field
/// (master spec section 7.1), and a field the parser was unsure about arrives
/// empty rather than guessed.
@immutable
class ParsedOrder {
  const ParsedOrder({
    required this.sourceText,
    required this.confidence,
    required this.warnings,
    required this.needsPhoneSelection,
    required this.isLowConfidence,
    this.customerName,
    this.phones = const <String>[],
    this.selectedPhone,
    this.address,
    this.items = const <ParsedItem>[],
    this.codAmountPaisa,
    this.notes,
  });

  factory ParsedOrder.fromJson(Map<String, dynamic> json) => ParsedOrder(
    customerName: json['customer_name'] as String?,
    phones: <String>[
      for (final phone
          in (json['phones'] as List<dynamic>? ?? const <dynamic>[]))
        phone as String,
    ],
    selectedPhone: json['selected_phone'] as String?,
    address: json['address'] as String?,
    items: <ParsedItem>[
      for (final item in (json['items'] as List<dynamic>? ?? const <dynamic>[]))
        ParsedItem.fromJson(item as Map<String, dynamic>),
    ],
    codAmountPaisa: json['cod_amount_paisa'] as int?,
    notes: json['notes'] as String?,
    confidence: <String, double>{
      for (final entry
          in (json['confidence'] as Map<String, dynamic>? ?? const {}).entries)
        entry.key: (entry.value as num).toDouble(),
    },
    warnings: <String>[
      for (final warning
          in (json['warnings'] as List<dynamic>? ?? const <dynamic>[]))
        warning as String,
    ],
    needsPhoneSelection: json['needs_phone_selection'] as bool? ?? false,
    isLowConfidence: json['is_low_confidence'] as bool? ?? false,
    sourceText: json['source_text'] as String? ?? '',
  );

  final String? customerName;

  /// Every number found. More than one means the seller must choose — the
  /// parser never picks for them (master spec section 8).
  final List<String> phones;
  final String? selectedPhone;

  final String? address;
  final List<ParsedItem> items;
  final int? codAmountPaisa;
  final String? notes;

  /// Per field, 0.0–1.0. Drives the "check this" markers on the review form.
  final Map<String, double> confidence;

  final List<String> warnings;
  final bool needsPhoneSelection;
  final bool isLowConfidence;

  /// Always the seller's original text, even on a total parse failure.
  final String sourceText;

  bool isUncertain(String field) => (confidence[field] ?? 0) < 0.6;
}

// --------------------------------------------------------------------------- //
// Imports
// --------------------------------------------------------------------------- //

@immutable
class ImportBatch {
  const ImportBatch({
    required this.id,
    required this.template,
    required this.status,
    required this.filename,
    required this.rowCount,
    required this.readyCount,
    required this.warningCount,
    required this.duplicateCount,
    required this.invalidCount,
    required this.createdCount,
    required this.canCommit,
    required this.detectedHeaders,
    required this.columnMapping,
  });

  factory ImportBatch.fromJson(Map<String, dynamic> json) => ImportBatch(
    id: json['id'] as String,
    template: json['template'] as String,
    status: json['status'] as String,
    filename: json['original_filename'] as String,
    rowCount: json['row_count'] as int? ?? 0,
    readyCount: json['ready_count'] as int? ?? 0,
    warningCount: json['warning_count'] as int? ?? 0,
    duplicateCount: json['duplicate_count'] as int? ?? 0,
    invalidCount: json['invalid_count'] as int? ?? 0,
    createdCount: json['created_count'] as int? ?? 0,
    canCommit: json['can_commit'] as bool? ?? false,
    detectedHeaders: <String>[
      for (final header
          in (json['detected_headers'] as List<dynamic>? ?? const <dynamic>[]))
        header as String,
    ],
    columnMapping: <String, String>{
      for (final entry
          in (json['column_mapping'] as Map<String, dynamic>? ?? const {})
              .entries)
        entry.key: entry.value as String,
    },
  );

  final String id;
  final String template;
  final String status;
  final String filename;
  final int rowCount;
  final int readyCount;
  final int warningCount;
  final int duplicateCount;
  final int invalidCount;
  final int createdCount;
  final bool canCommit;
  final List<String> detectedHeaders;
  final Map<String, String> columnMapping;

  bool get isValidated => status == 'VALIDATED';
  bool get isCommitted => status == 'COMMITTED';
  int get importableCount => readyCount + warningCount;
}

@immutable
class ImportRowReport {
  const ImportRowReport({
    required this.rowNumber,
    required this.status,
    required this.raw,
    required this.errors,
    required this.warnings,
  });

  factory ImportRowReport.fromJson(Map<String, dynamic> json) =>
      ImportRowReport(
        rowNumber: json['row_number'] as int,
        status: json['status'] as String,
        raw: Map<String, dynamic>.from(json['raw'] as Map? ?? const {}),
        errors: <String>[
          for (final error
              in (json['errors'] as List<dynamic>? ?? const <dynamic>[]))
            '${(error as Map)['field']}: ${error['message']}',
        ],
        warnings: <String>[
          for (final warning
              in (json['warnings'] as List<dynamic>? ?? const <dynamic>[]))
            '${(warning as Map)['field']}: ${warning['message']}',
        ],
      );

  final int rowNumber;
  final String status;
  final Map<String, dynamic> raw;
  final List<String> errors;
  final List<String> warnings;
}

// --------------------------------------------------------------------------- //
// Paging
// --------------------------------------------------------------------------- //

/// One page of a cursor-paginated list.
@immutable
class PagedResult<T> {
  const PagedResult({
    required this.items,
    this.nextCursor,
    this.hasMore = false,
  });

  final List<T> items;
  final String? nextCursor;
  final bool hasMore;

  static PagedResult<T> parse<T>(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) fromJson,
  ) {
    return PagedResult<T>(
      items: <T>[
        for (final item
            in (json['items'] as List<dynamic>? ?? const <dynamic>[]))
          fromJson(item as Map<String, dynamic>),
      ],
      nextCursor: json['next_cursor'] as String?,
      hasMore: json['has_more'] as bool? ?? false,
    );
  }
}
