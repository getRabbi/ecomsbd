import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/chat_orders/chat_orders.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../design/components/badges.dart';
import '../../design/tokens.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/intake_strings.dart';
import '../orders/duplicate_warning_sheet.dart';
import '../orders/product_picker_sheet.dart';
import '../risk/external_risk_card.dart';
import '../shared/inputs.dart';

/// What became of **Create order** on the quick-order sheet.
@immutable
class QuickOrderOutcome {
  const QuickOrderOutcome({required this.order, required this.queued});

  final SellerOrder order;

  /// Offline: the order waits in the outbox and is sent on reconnect.
  final bool queued;
}

/// One line on the sheet.
class _Line {
  _Line(ChatDraftItem item)
    : match = item.match,
      productId = item.match.isMatched ? item.match.productId : null,
      productName = item.match.isMatched ? item.match.productName : null,
      variantId = item.match.variantId,
      variantName = item.match.variantName,
      needsVariant =
          item.match.isMatched && item.match.variantStatus == 'AMBIGUOUS',
      name = TextEditingController(
        text: item.match.isMatched
            ? (item.match.productName ?? item.name)
            : <String>[item.name, ?item.size].join(' '),
      ),
      quantity = TextEditingController(text: '${item.quantity}'),
      price = TextEditingController(
        text: item.match.unitPricePaisa == null
            ? ''
            : _taka(item.match.unitPricePaisa!),
      );

  _Line.empty()
    : match = const ChatDraftMatch(status: 'NOT_FOUND'),
      needsVariant = false,
      name = TextEditingController(),
      quantity = TextEditingController(text: '1'),
      price = TextEditingController();

  final ChatDraftMatch match;
  final TextEditingController name;
  final TextEditingController quantity;
  final TextEditingController price;
  String? productId;
  String? productName;
  String? variantId;
  String? variantName;
  bool needsVariant;

  /// The parser could not pin it to one product: the seller picks.
  bool get needsProduct => match.status == 'AMBIGUOUS' && productId == null;

  bool get notInCatalog => match.status == 'NOT_FOUND' && productId == null;

  void dispose() {
    name.dispose();
    quantity.dispose();
    price.dispose();
  }
}

String _taka(int paisa) =>
    (paisa / 100).toStringAsFixed(paisa % 100 == 0 ? 0 : 2);

/// Taka typed by the seller (Bangla or ASCII digits) as paisa; blank is 0,
/// anything else unreadable is null.
int? _paisa(String raw) {
  final text = normalizeDigits(raw).replaceAll(',', '').trim();
  if (text.isEmpty) return 0;
  final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
  if (match == null) return null;
  return int.parse(match.group(1)!) * 100 +
      int.parse((match.group(2) ?? '').padRight(2, '0'));
}

/// The order form on the quick-order sheet (V4 WP1.5).
///
/// Every field comes from the server's draft (the LLM path when the shop has
/// `ai_parse_enabled`) and is the seller's to edit: fields the reader was
/// unsure of are marked, a chat with several numbers asks which one, a line
/// the catalogue could not pin down asks for the product, and the COD shows
/// where it came from. **Create order** confirms the draft (one order,
/// however often it is sent); offline it goes through the outbox with the
/// draft id as its client id, so the server closes the draft when it lands.
/// Until Phase 2 this one button stands for "Confirm & call customer" and
/// "Already confirmed in chat" (WP2.1 splits it behind
/// `confirmation_engine_enabled`).
class QuickOrderForm extends ConsumerStatefulWidget {
  const QuickOrderForm({
    required this.draft,
    required this.onDone,
    required this.onSaveDraft,
    required this.onMoreMessages,
    this.suggestion,
    super.key,
  });

  final ChatDraft draft;
  final CourierSuggestion? suggestion;
  final ValueChanged<QuickOrderOutcome> onDone;
  final VoidCallback onSaveDraft;
  final VoidCallback onMoreMessages;

  @override
  ConsumerState<QuickOrderForm> createState() => _QuickOrderFormState();
}

class _QuickOrderFormState extends ConsumerState<QuickOrderForm> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();
  late final TextEditingController _phone;
  late final TextEditingController _name;
  late final TextEditingController _address;
  late final TextEditingController _cod;
  late List<_Line> _lines;
  bool _saving = false;
  ApiError? _error;

  ChatDraft get _draft => widget.draft;

  bool get _choosePhone =>
      _draft.phones.length > 1 && _draft.selectedPhone == null;

  @override
  void initState() {
    super.initState();
    final draft = _draft;
    _phone = TextEditingController(
      text: _choosePhone
          ? ''
          : (draft.selectedPhone ??
                (draft.phones.isEmpty ? '' : draft.phones.first)),
    );
    _name = TextEditingController(text: draft.customerName ?? '');
    _address = TextEditingController(text: draft.address ?? '');
    _cod = TextEditingController(
      text: draft.codAmountPaisa == null ? '' : _taka(draft.codAmountPaisa!),
    );
    _lines = draft.items.isEmpty
        ? <_Line>[_Line.empty()]
        : <_Line>[for (final item in draft.items) _Line(item)];
  }

  @override
  void dispose() {
    _phone.dispose();
    _name.dispose();
    _address.dispose();
    _cod.dispose();
    for (final line in _lines) {
      line.dispose();
    }
    super.dispose();
  }

  bool _marked(String field) =>
      _draft.isUncertain(field) || _draft.isMissing(field);

  int get _subtotal {
    var total = 0;
    for (final line in _lines) {
      final quantity =
          int.tryParse(normalizeDigits(line.quantity.text).trim()) ?? 0;
      total += (_paisa(line.price.text) ?? 0) * quantity;
    }
    return total;
  }

  /// Blank COD means "the items total", as the hint under it shows.
  int get _codAmount =>
      _cod.text.trim().isEmpty ? _subtotal : (_paisa(_cod.text) ?? _subtotal);

  Future<void> _choose(_Line line) async {
    final picked = await ProductPickerSheet.show(
      context,
      suggestions: line.match.candidates,
      initialQuery: line.productId == null ? line.name.text.trim() : '',
      productId: line.needsVariant ? line.productId : null,
    );
    if (picked == null || !mounted) return;
    setState(() {
      line
        ..productId = picked.product.id
        ..productName = picked.product.name
        ..variantId = picked.variant?.id
        ..variantName = picked.variant?.name
        ..needsVariant = false;
      line.name.text = picked.product.name;
      if ((_paisa(line.price.text) ?? 0) == 0) {
        line.price.text = _taka(picked.product.sellingPrice.paisa);
      }
    });
  }

  ApiError _invalid(String key) => ApiError(
    code: ApiErrorCode.validation,
    messageBn: intakeBn[key]!,
    messageEn: intakeEn[key]!,
    retryable: false,
  );

  Future<void> _create() async {
    if (!(_form.currentState?.validate() ?? false)) return;
    final named = _lines.where((line) => line.name.text.trim().isNotEmpty);
    if (named.any((line) => line.needsProduct || line.needsVariant)) {
      setState(() => _error = _invalid('qo.chooseProduct'));
      return;
    }
    final items = <Map<String, dynamic>>[
      for (final line in named)
        <String, dynamic>{
          'product_id': ?line.productId,
          'variant_id': ?line.variantId,
          'name': line.name.text.trim(),
          'quantity':
              int.tryParse(normalizeDigits(line.quantity.text).trim()) ?? 1,
          if ((_paisa(line.price.text) ?? 0) > 0)
            'unit_price_paisa': _paisa(line.price.text),
        },
    ];
    if (items.isEmpty) {
      setState(() => _error = _invalid('qo.itemsRequired'));
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    final phone = _phone.text.trim();
    final orders = ref.read(ordersRepositoryProvider);
    try {
      final check = await orders.checkDuplicates(
        phone: phone,
        codAmountPaisa: _codAmount,
        itemNames: <String>[for (final item in items) item['name'] as String],
      );
      if (check.possibleDuplicate && mounted) {
        final keep = await DuplicateWarningSheet.show(context, check);
        if (!keep) {
          if (mounted) setState(() => _saving = false);
          return;
        }
      }
    } on ApiError {
      // Offline, or the check failed: the order matters more than the warning.
    }
    final body = <String, dynamic>{
      'phone': phone,
      'items': items,
      if (_name.text.trim().isNotEmpty) 'customer_name': _name.text.trim(),
      if (_address.text.trim().isNotEmpty) 'address': _address.text.trim(),
      'cod_amount_paisa': _codAmount,
    };
    final chats = ref.read(chatOrdersRepositoryProvider);
    try {
      final result = await chats.confirm(_draft.id, body);
      if (mounted) {
        widget.onDone(QuickOrderOutcome(order: result.order, queued: false));
      }
    } on ApiError catch (error) {
      if (!error.isOffline) {
        if (mounted) {
          setState(() {
            _saving = false;
            _error = error;
          });
        }
        return;
      }
      await _createThroughOutbox(body, items);
    }
  }

  /// Offline: the existing outbox, with the draft id as the client id (the
  /// id confirming uses), so the queued create and a later confirm are one
  /// order and the server closes the draft when the create lands.
  Future<void> _createThroughOutbox(
    Map<String, dynamic> body,
    List<Map<String, dynamic>> items,
  ) async {
    try {
      final saved = await ref
          .read(ordersRepositoryProvider)
          .create(
            phone: body['phone'] as String,
            items: items,
            customerName: body['customer_name'] as String?,
            address: body['address'] as String?,
            codAmountPaisa: body['cod_amount_paisa'] as int,
            channel: 'PASTE_PARSE',
            clientId: _draft.id,
          );
      var order = saved.order;
      if (!saved.isQueued) {
        // Back online between the two calls: confirming now replays that
        // order (same client id) and closes the draft.
        try {
          order =
              (await ref
                      .read(chatOrdersRepositoryProvider)
                      .confirm(_draft.id, body))
                  .order;
        } on ApiError {
          // The order exists; the draft closes on the next confirm.
        }
      }
      if (mounted) {
        widget.onDone(QuickOrderOutcome(order: order, queued: saved.isQueued));
      }
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = error;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final draft = _draft;
    final theme = Theme.of(context);
    final bn = context.strings.locale == AppLocale.bn;
    final customer = draft.customer;
    final anyMarked = <String>[
      'name',
      'phone',
      'address',
      'amount',
      'product',
      'variant',
    ].any(draft.isUncertain);
    final error = _error;
    return Form(
      key: _form,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          if (draft.warnings.contains('DUPLICATE_ORDER'))
            _Notice(
              key: const ValueKey('qo.duplicate'),
              text: context.tr('qo.duplicate'),
              tone: Tone.warning,
            ),
          if (draft.warnings.contains('CUSTOMER_BLOCKED'))
            _Notice(text: context.tr('qo.blocked'), tone: Tone.bad),
          if (anyMarked)
            _Notice(
              key: const ValueKey('qo.checkMarked'),
              text: context.tr('qo.checkMarked'),
              tone: Tone.info,
            ),
          if (customer != null) ...<Widget>[
            Text(
              context.tr('qo.knownCustomer', <String, Object?>{
                'name': customer.name ?? customer.phoneMasked ?? '',
                'count': customer.orderCount,
              }),
              style: theme.textTheme.bodySmall,
            ),
            CourierHistoryChip(customerId: customer.id),
            const SizedBox(height: EcomsbdSpacing.sm),
          ],
          if (_choosePhone) ...<Widget>[
            Text(
              context.tr('qo.choosePhone'),
              style: theme.textTheme.bodySmall,
            ),
            Wrap(
              spacing: EcomsbdSpacing.xs,
              children: <Widget>[
                for (var i = 0; i < draft.phones.length; i++)
                  ChoiceChip(
                    key: ValueKey('qo.phone.$i'),
                    label: Text(draft.phones[i]),
                    selected: _phone.text == draft.phones[i],
                    onSelected: (_) =>
                        setState(() => _phone.text = draft.phones[i]),
                  ),
              ],
            ),
            const SizedBox(height: EcomsbdSpacing.xs),
          ],
          _Marked(
            show: _marked('phone'),
            child: LabelledField(
              key: const ValueKey('qo.phoneField'),
              label: context.tr('qo.phone'),
              controller: _phone,
              keyboardType: TextInputType.phone,
              inputFormatters: <TextInputFormatter>[
                FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯+\- ]')),
              ],
              validator: (value) => (value == null || value.trim().length < 6)
                  ? context.tr('qo.phoneRequired')
                  : null,
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _Marked(
            show: _marked('name'),
            child: LabelledField(
              label: context.tr('qo.name'),
              controller: _name,
              hint: context.tr('common.optional'),
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          _Marked(
            show: _marked('address'),
            child: LabelledField(
              key: const ValueKey('qo.addressField'),
              label: context.tr('qo.address'),
              controller: _address,
              maxLines: 2,
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          Text(context.tr('qo.items'), style: theme.textTheme.labelLarge),
          for (var i = 0; i < _lines.length; i++)
            _LineRow(
              key: ValueKey('qo.line.$i'),
              index: i,
              line: _lines[i],
              onChanged: () => setState(() {}),
              onChoose: () => unawaited(_choose(_lines[i])),
            ),
          const SizedBox(height: EcomsbdSpacing.md),
          _Marked(
            show: _marked('amount'),
            child: LabelledField(
              key: const ValueKey('qo.codField'),
              label: context.tr('qo.cod'),
              controller: _cod,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              inputFormatters: <TextInputFormatter>[
                FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯.,]')),
              ],
              hint: _subtotal == 0 ? null : Money(_subtotal).format(),
              validator: (value) => _paisa(value ?? '') == null
                  ? context.tr('qo.amountError')
                  : null,
            ),
          ),
          if (draft.warnings.contains('COD_MISMATCH'))
            _Notice(
              key: const ValueKey('qo.codMismatch'),
              text: context.tr('qo.codMismatch'),
              tone: Tone.warning,
            )
          else if (draft.codSource == 'CATALOG')
            _Caption(
              key: const ValueKey('qo.codCatalog'),
              text: context.tr('qo.codCatalog'),
            ),
          if (draft.warnings.contains('ZONE_UNKNOWN'))
            _Caption(
              key: const ValueKey('qo.zoneUnknown'),
              text: context.tr('qo.zoneUnknown'),
            ),
          const SizedBox(height: EcomsbdSpacing.sm),
          if (widget.suggestion != null)
            _CourierLine(suggestion: widget.suggestion!),
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.sm),
              child: Text(
                bn ? error.messageBn : error.messageEn,
                key: const ValueKey('qo.formError'),
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          const SizedBox(height: EcomsbdSpacing.md),
          FilledButton(
            key: const ValueKey('qo.create'),
            onPressed: _saving ? null : () => unawaited(_create()),
            style: FilledButton.styleFrom(
              backgroundColor: EcomsbdColors.orange,
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
            ),
            child: Text(
              _saving ? context.tr('qo.creating') : context.tr('qo.create'),
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          OutlinedButton(
            key: const ValueKey('qo.saveDraft'),
            onPressed: _saving ? null : widget.onSaveDraft,
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
              shape: const StadiumBorder(),
            ),
            child: Text(context.tr('qo.saveDraft')),
          ),
          _Caption(text: context.tr('qo.saveDraftNote')),
          TextButton.icon(
            key: const ValueKey('qo.more'),
            onPressed: _saving ? null : widget.onMoreMessages,
            icon: const Icon(Icons.content_paste_rounded, size: 17),
            label: Text(context.tr('qo.more')),
          ),
        ],
      ),
    );
  }
}

class _LineRow extends StatelessWidget {
  const _LineRow({
    required this.index,
    required this.line,
    required this.onChanged,
    required this.onChoose,
    super.key,
  });

  final int index;
  final _Line line;
  final VoidCallback onChanged;
  final VoidCallback onChoose;

  @override
  Widget build(BuildContext context) {
    final choose = line.needsProduct || line.needsVariant;
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          LabelledField(
            label: '${context.tr('qo.item')} ${index + 1}',
            controller: line.name,
            onChanged: (_) => onChanged(),
          ),
          const SizedBox(height: EcomsbdSpacing.xs),
          Row(
            children: <Widget>[
              Expanded(
                child: LabelledField(
                  label: context.tr('qo.qty'),
                  controller: line.quantity,
                  keyboardType: TextInputType.number,
                  inputFormatters: <TextInputFormatter>[
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯]')),
                  ],
                  onChanged: (_) => onChanged(),
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                flex: 2,
                child: LabelledField(
                  label: context.tr('qo.price'),
                  controller: line.price,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  inputFormatters: <TextInputFormatter>[
                    FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯.,]')),
                  ],
                  onChanged: (_) => onChanged(),
                ),
              ),
            ],
          ),
          if (choose)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.xxs),
              child: OutlinedButton.icon(
                key: ValueKey('qo.pick.$index'),
                onPressed: onChoose,
                icon: const Icon(Icons.search_rounded, size: 17),
                label: Text(
                  context.tr(line.needsProduct ? 'qo.pick' : 'qo.pickVariant'),
                ),
              ),
            )
          else if (line.notInCatalog && line.name.text.trim().isNotEmpty)
            _Caption(text: context.tr('qo.notInCatalog')),
        ],
      ),
    );
  }
}

class _CourierLine extends StatelessWidget {
  const _CourierLine({required this.suggestion});

  final CourierSuggestion suggestion;

  @override
  Widget build(BuildContext context) {
    final courier = courierLabel(suggestion.courier);
    final text = suggestion.fromHistory
        ? context.tr('qo.courierHistory', <String, Object?>{
            'courier': courier,
            'delivered': suggestion.delivered ?? 0,
            'parcels': suggestion.parcels ?? 0,
          })
        : context.tr('qo.courierDefault', <String, Object?>{
            'courier': courier,
          });
    return Column(
      key: const ValueKey('qo.courier'),
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Row(
          children: <Widget>[
            const Icon(
              Icons.local_shipping_outlined,
              size: 18,
              color: EcomsbdColors.blue,
            ),
            const SizedBox(width: EcomsbdSpacing.xs),
            Expanded(child: Text(text)),
          ],
        ),
        _Caption(text: context.tr('qo.courierNote')),
      ],
    );
  }
}

/// A field the reader was unsure of, or that the chat did not have.
class _Marked extends StatelessWidget {
  const _Marked({required this.show, required this.child});

  final bool show;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (!show) return child;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        child,
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: StatusChip(
            label: context.tr('imp.checkThis'),
            tone: Tone.warning,
            icon: Icons.edit_outlined,
          ),
        ),
      ],
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text, required this.tone, super.key});

  final String text;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    // Wraps: a chip would cut a sentence off on a 360 px phone.
    return Container(
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.xs),
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: tone.surface,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(tone.icon, size: 16, color: tone.ink),
          const SizedBox(width: EcomsbdSpacing.xs),
          Expanded(
            child: Text(text, style: TextStyle(color: tone.ink)),
          ),
        ],
      ),
    );
  }
}

class _Caption extends StatelessWidget {
  const _Caption({required this.text, super.key});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.xxs),
      child: Text(
        text,
        style: Theme.of(
          context,
        ).textTheme.bodySmall?.copyWith(color: EcomsbdColors.muted),
      ),
    );
  }
}
