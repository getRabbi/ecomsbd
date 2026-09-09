import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/models.dart';
import '../../data/commerce/orders_repository.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/inputs.dart';
import 'duplicate_warning_sheet.dart';

/// A line the seller is composing.
class _DraftItem {
  _DraftItem({String? name, int quantity = 1, int unitPricePaisa = 0})
    : name = TextEditingController(text: name ?? ''),
      quantity = TextEditingController(text: '$quantity'),
      price = TextEditingController(
        text: unitPricePaisa == 0 ? '' : _taka(unitPricePaisa),
      );

  final TextEditingController name;
  final TextEditingController quantity;
  final TextEditingController price;

  String? productId;

  static String _taka(int paisa) =>
      (paisa / 100).toStringAsFixed(paisa % 100 == 0 ? 0 : 2);

  void dispose() {
    name.dispose();
    quantity.dispose();
    price.dispose();
  }
}

/// Create an order — typed in, or pasted from Messenger.
///
/// Both routes end at the same review form, because the parser's output is a
/// *suggestion*: master spec section 7.1 requires the seller to confirm and
/// edit every field before anything is saved, and a field the parser was unsure
/// about arrives empty rather than guessed.
///
/// Saving creates the order and stops. No courier is contacted — booking is an
/// explicit, separate action, because an external create is irreversible
/// (section 62.17).
class OrderComposeScreen extends ConsumerStatefulWidget {
  const OrderComposeScreen({super.key, this.startWithPaste = false});

  /// Open straight on the paste tab.
  final bool startWithPaste;

  @override
  ConsumerState<OrderComposeScreen> createState() => _OrderComposeScreenState();
}

class _OrderComposeScreenState extends ConsumerState<OrderComposeScreen> {
  final GlobalKey<FormState> _form = GlobalKey<FormState>();

  final TextEditingController _paste = TextEditingController();
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _name = TextEditingController();
  final TextEditingController _address = TextEditingController();
  final TextEditingController _district = TextEditingController();
  final TextEditingController _area = TextEditingController();
  final TextEditingController _cod = TextEditingController();
  final TextEditingController _delivery = TextEditingController();
  final TextEditingController _note = TextEditingController();

  List<_DraftItem> _items = <_DraftItem>[_DraftItem()];

  /// True while the seller is on the paste step.
  late bool _pasting = widget.startWithPaste;

  bool _parsing = false;
  bool _saving = false;
  ApiError? _error;
  ParsedOrder? _parsed;
  Customer? _knownCustomer;

  /// Fields the parser was unsure about, marked for the seller to check.
  Set<String> _uncertain = <String>{};

  String? _sourceText;

  @override
  void dispose() {
    _paste.dispose();
    _phone.dispose();
    _name.dispose();
    _address.dispose();
    _district.dispose();
    _area.dispose();
    _cod.dispose();
    _delivery.dispose();
    _note.dispose();
    for (final item in _items) {
      item.dispose();
    }
    super.dispose();
  }

  int? _paisa(String raw) {
    final text = normalizeDigits(raw).replaceAll(',', '').trim();
    if (text.isEmpty) {
      return 0;
    }
    final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
    if (match == null) {
      return null;
    }
    return int.parse(match.group(1)!) * 100 +
        int.parse((match.group(2) ?? '').padRight(2, '0'));
  }

  /// What the seller will collect.
  ///
  /// A blank COD field means "the items total", which is exactly what the hint
  /// under it shows. Reading blank as ৳0 would send a zero-COD order for a
  /// parcel the courier is about to collect money for.
  int get _codAmount {
    if (_cod.text.trim().isEmpty) {
      return _subtotal;
    }
    return _paisa(_cod.text) ?? _subtotal;
  }

  int get _subtotal {
    var total = 0;
    for (final item in _items) {
      final quantity =
          int.tryParse(normalizeDigits(item.quantity.text).trim()) ?? 0;
      total += (_paisa(item.price.text) ?? 0) * quantity;
    }
    return total;
  }

  // --- parsing --------------------------------------------------------------

  Future<void> _parse() async {
    final text = _paste.text.trim();
    if (text.isEmpty) {
      return;
    }
    setState(() {
      _parsing = true;
      _error = null;
    });

    try {
      final parsed = await ref.read(ordersRepositoryProvider).parse(text);
      if (!mounted) {
        return;
      }
      _applyParsed(parsed);
    } on ApiError catch (error) {
      if (mounted) {
        setState(() {
          _parsing = false;
          _error = error;
        });
      }
    }
  }

  void _applyParsed(ParsedOrder parsed) {
    for (final item in _items) {
      item.dispose();
    }

    setState(() {
      _parsing = false;
      _parsed = parsed;
      // Always kept, even on a total parse failure: the seller's original text
      // must never be the thing that gets lost.
      _sourceText = parsed.sourceText;
      _name.text = parsed.customerName ?? '';
      _phone.text = parsed.needsPhoneSelection
          ? ''
          : (parsed.selectedPhone ?? '');
      _address.text = parsed.address ?? '';
      _cod.text = parsed.codAmountPaisa == null
          ? ''
          : _DraftItem._taka(parsed.codAmountPaisa!);
      _note.text = parsed.notes ?? '';
      _items = parsed.items.isEmpty
          ? <_DraftItem>[_DraftItem()]
          : <_DraftItem>[
              for (final item in parsed.items)
                _DraftItem(
                  name: item.displayName,
                  quantity: item.quantity,
                  unitPricePaisa: item.unitPricePaisa ?? 0,
                ),
            ];
      _uncertain = <String>{
        for (final field in <String>['name', 'phone', 'address', 'amount'])
          if (parsed.isUncertain(field)) field,
      };
      _pasting = false;
    });
  }

  Future<void> _lookupCustomer() async {
    final phone = _phone.text.trim();
    if (phone.length < 6) {
      return;
    }
    try {
      final customer = await ref
          .read(customersRepositoryProvider)
          .lookupByPhone(phone);
      if (mounted) {
        setState(() => _knownCustomer = customer);
      }
    } on ApiError {
      // A failed lookup is not worth interrupting order entry for; the server
      // resolves the customer again when the order is saved.
    }
  }

  // --- saving ---------------------------------------------------------------

  Future<void> _save() async {
    if (!(_form.currentState?.validate() ?? false)) {
      return;
    }
    final items = <Map<String, dynamic>>[
      for (final item in _items)
        if (item.name.text.trim().isNotEmpty)
          <String, dynamic>{
            if (item.productId != null) 'product_id': item.productId,
            'name': item.name.text.trim(),
            'quantity':
                int.tryParse(normalizeDigits(item.quantity.text).trim()) ?? 1,
            if ((_paisa(item.price.text) ?? 0) > 0)
              'unit_price_paisa': _paisa(item.price.text),
          },
    ];
    if (items.isEmpty) {
      setState(
        () => _error = const ApiError(
          code: ApiErrorCode.validation,
          messageBn: 'অন্তত একটি পণ্য যোগ করুন।',
          messageEn: 'Add at least one item.',
          retryable: false,
        ),
      );
      return;
    }

    setState(() {
      _saving = true;
      _error = null;
    });

    final repository = ref.read(ordersRepositoryProvider);

    // Ask before saving, so the warning arrives while the seller can still act
    // on it. This never blocks the save; it only offers the choice.
    try {
      final check = await repository.checkDuplicates(
        phone: _phone.text.trim(),
        codAmountPaisa: _codAmount,
        itemNames: <String>[for (final item in items) item['name'] as String],
      );
      if (check.possibleDuplicate && mounted) {
        final keep = await DuplicateWarningSheet.show(context, check);
        if (!keep) {
          if (mounted) {
            setState(() => _saving = false);
          }
          return;
        }
      }
    } on ApiError {
      // Offline, or the check failed. Creating the order is more important than
      // the warning, and the server checks again on create.
    }

    try {
      final saved = await repository.create(
        phone: _phone.text.trim(),
        items: items,
        customerName: _name.text.trim(),
        address: _address.text.trim(),
        district: _district.text.trim(),
        area: _area.text.trim(),
        codAmountPaisa: _codAmount,
        deliveryFeePaisa: _paisa(_delivery.text) ?? 0,
        note: _note.text.trim(),
        sourceText: _sourceText,
        channel: _parsed == null ? 'MANUAL' : 'PASTE',
      );
      if (mounted) {
        Navigator.of(context).pop(saved);
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

  // --- build ----------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return DetailScaffold(
      title: _pasting ? 'Paste an order' : 'New order',
      subtitle: _pasting ? 'Messenger, WhatsApp, anywhere' : null,
      children: _pasting ? _pasteStep() : _reviewStep(),
    );
  }

  List<Widget> _pasteStep() {
    return <Widget>[
      GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            const Text(
              'Paste the customer’s message',
              style: EcomsbdType.bodyStrong,
            ),
            const SizedBox(height: 3),
            Text(
              'Nothing is saved yet. You will see every field before anything '
              'is created, and anything unclear is left blank for you.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
            const SizedBox(height: EcomsbdSpacing.md),
            LabelledField(
              label: 'Message',
              controller: _paste,
              maxLines: 8,
              hint:
                  'Nusrat Jahan\n01712345678\nHouse 4, Road 2, Dhanmondi\n'
                  '1 Cotton Abaya XL\nCOD 1250',
            ),
          ],
        ),
      ),
      if (_error != null) ...<Widget>[
        const SizedBox(height: EcomsbdSpacing.sm),
        ErrorStateCard(error: _error!, onRetry: _parse),
      ],
      const SizedBox(height: EcomsbdSpacing.md),
      FilledButton(
        onPressed: _parsing ? null : _parse,
        style: FilledButton.styleFrom(
          backgroundColor: EcomsbdColors.orange,
          minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
          textStyle: EcomsbdType.label,
        ),
        child: Text(_parsing ? 'Reading…' : 'Read the message'),
      ),
      const SizedBox(height: EcomsbdSpacing.sm),
      TextButton(
        onPressed: () => setState(() => _pasting = false),
        style: TextButton.styleFrom(
          minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
          foregroundColor: EcomsbdColors.muted,
          textStyle: EcomsbdType.label,
        ),
        child: const Text('Type it in instead'),
      ),
    ];
  }

  List<Widget> _reviewStep() {
    final parsed = _parsed;

    return <Widget>[
      if (parsed != null) ...<Widget>[
        _ParseSummary(
          parsed: parsed,
          onEditText: () => setState(() => _pasting = true),
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      if (parsed != null && parsed.needsPhoneSelection) ...<Widget>[
        _PhoneChoice(
          phones: parsed.phones,
          selected: _phone.text,
          onSelected: (phone) {
            setState(() => _phone.text = phone);
            _lookupCustomer();
          },
        ),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      if (_error != null) ...<Widget>[
        ErrorStateCard(error: _error!),
        const SizedBox(height: EcomsbdSpacing.sm),
      ],
      Form(
        key: _form,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            GlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Text('Customer', style: EcomsbdType.bodyStrong),
                  const SizedBox(height: EcomsbdSpacing.md),
                  _CheckThis(
                    show: _uncertain.contains('phone'),
                    child: LabelledField(
                      label: 'Phone',
                      controller: _phone,
                      keyboardType: TextInputType.phone,
                      inputFormatters: <TextInputFormatter>[
                        FilteringTextInputFormatter.allow(
                          RegExp(r'[0-9০-৯+\- ]'),
                        ),
                      ],
                      onChanged: (_) => _knownCustomer == null
                          ? null
                          : setState(() => _knownCustomer = null),
                      validator: (value) =>
                          (value == null || value.trim().length < 6)
                          ? 'A phone number is required'
                          : null,
                    ),
                  ),
                  if (_knownCustomer != null) ...<Widget>[
                    const SizedBox(height: EcomsbdSpacing.xs),
                    _KnownCustomerNote(customer: _knownCustomer!),
                  ],
                  const SizedBox(height: EcomsbdSpacing.md),
                  _CheckThis(
                    show: _uncertain.contains('name'),
                    child: LabelledField(
                      label: 'Name',
                      controller: _name,
                      hint: 'Optional',
                    ),
                  ),
                  const SizedBox(height: EcomsbdSpacing.md),
                  _CheckThis(
                    show: _uncertain.contains('address'),
                    child: LabelledField(
                      label: 'Delivery address',
                      controller: _address,
                      maxLines: 3,
                    ),
                  ),
                  const SizedBox(height: EcomsbdSpacing.md),
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: LabelledField(
                          label: 'District',
                          controller: _district,
                          hint: 'Optional',
                        ),
                      ),
                      const SizedBox(width: EcomsbdSpacing.sm),
                      Expanded(
                        child: LabelledField(
                          label: 'Area',
                          controller: _area,
                          hint: 'Optional',
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            GlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Row(
                    children: <Widget>[
                      const Expanded(
                        child: Text('Items', style: EcomsbdType.bodyStrong),
                      ),
                      TextButton.icon(
                        onPressed: () =>
                            setState(() => _items.add(_DraftItem())),
                        icon: const Icon(Icons.add_rounded, size: 17),
                        label: const Text('Add item'),
                        style: TextButton.styleFrom(
                          foregroundColor: EcomsbdColors.orange,
                          minimumSize: const Size(0, EcomsbdTouch.minTarget),
                          textStyle: EcomsbdType.chip,
                        ),
                      ),
                    ],
                  ),
                  for (var i = 0; i < _items.length; i++) ...<Widget>[
                    const SizedBox(height: EcomsbdSpacing.sm),
                    _ItemRow(
                      item: _items[i],
                      index: i,
                      canRemove: _items.length > 1,
                      onRemove: () => setState(() {
                        _items.removeAt(i).dispose();
                      }),
                      onChanged: () => setState(() {}),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            GlassCard(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Text('Money', style: EcomsbdType.bodyStrong),
                  const SizedBox(height: EcomsbdSpacing.md),
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: _CheckThis(
                          show: _uncertain.contains('amount'),
                          child: LabelledField(
                            label: 'COD to collect (৳)',
                            controller: _cod,
                            keyboardType: const TextInputType.numberWithOptions(
                              decimal: true,
                            ),
                            inputFormatters: <TextInputFormatter>[
                              FilteringTextInputFormatter.allow(
                                RegExp(r'[0-9০-৯.]'),
                              ),
                            ],
                            hint: _subtotal == 0
                                ? null
                                : Money(_subtotal).format(),
                            validator: (value) => _paisa(value ?? '') == null
                                ? 'Enter an amount like 1250'
                                : null,
                          ),
                        ),
                      ),
                      const SizedBox(width: EcomsbdSpacing.sm),
                      Expanded(
                        child: LabelledField(
                          label: 'Delivery fee (৳)',
                          controller: _delivery,
                          keyboardType: const TextInputType.numberWithOptions(
                            decimal: true,
                          ),
                          inputFormatters: <TextInputFormatter>[
                            FilteringTextInputFormatter.allow(
                              RegExp(r'[0-9০-৯.]'),
                            ),
                          ],
                          hint: 'Optional',
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: EcomsbdSpacing.md),
                  Row(
                    children: <Widget>[
                      Expanded(
                        child: Text(
                          'Items total',
                          style: EcomsbdType.caption.copyWith(
                            color: EcomsbdColors.muted,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: EcomsbdSpacing.xs),
                      MoneyText(
                        Money(_subtotal),
                        style: EcomsbdType.bodyStrong,
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            GlassCard(
              child: LabelledField(
                label: 'Note',
                controller: _note,
                maxLines: 3,
                hint: 'Optional — anything the courier should know',
              ),
            ),
            const SizedBox(height: EcomsbdSpacing.sm),
            const _NoCourierNote(),
            const SizedBox(height: EcomsbdSpacing.md),
            FilledButton(
              onPressed: _saving ? null : _save,
              style: FilledButton.styleFrom(
                backgroundColor: EcomsbdColors.orange,
                minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                shape: const StadiumBorder(),
                textStyle: EcomsbdType.label,
              ),
              child: Text(_saving ? 'Saving…' : 'Save order'),
            ),
            if (parsed == null) ...<Widget>[
              const SizedBox(height: EcomsbdSpacing.sm),
              TextButton.icon(
                onPressed: () => setState(() => _pasting = true),
                icon: const Icon(Icons.content_paste_rounded, size: 17),
                label: const Text('Paste a message instead'),
                style: TextButton.styleFrom(
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  foregroundColor: EcomsbdColors.muted,
                  textStyle: EcomsbdType.label,
                ),
              ),
            ],
          ],
        ),
      ),
    ];
  }
}

/// What the parser found, and what it could not.
class _ParseSummary extends StatelessWidget {
  const _ParseSummary({required this.parsed, required this.onEditText});

  final ParsedOrder parsed;
  final VoidCallback onEditText;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                parsed.isLowConfidence
                    ? Icons.help_outline_rounded
                    : Icons.auto_awesome_outlined,
                size: 18,
                color: parsed.isLowConfidence
                    ? EcomsbdColors.amber
                    : EcomsbdColors.blue,
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              const Expanded(
                child: Text(
                  'Check before saving',
                  style: EcomsbdType.bodyStrong,
                ),
              ),
              TextButton(
                onPressed: onEditText,
                style: TextButton.styleFrom(
                  foregroundColor: EcomsbdColors.orange,
                  minimumSize: const Size(0, EcomsbdTouch.minTarget),
                  textStyle: EcomsbdType.chip,
                ),
                child: const Text('Edit text'),
              ),
            ],
          ),
          const SizedBox(height: 3),
          Text(
            parsed.isLowConfidence
                ? 'Not much could be read from that message. Fill in what is '
                      'missing — nothing has been guessed.'
                : 'Everything below came from your message. Anything unclear '
                      'was left blank rather than guessed.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          if (parsed.warnings.isNotEmpty) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            for (final warning in parsed.warnings)
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    const Icon(
                      Icons.info_outline,
                      size: 14,
                      color: EcomsbdColors.amber,
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        warning,
                        style: EcomsbdType.caption.copyWith(
                          color: EcomsbdColors.amber,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

/// The parser found more than one number and will not choose.
class _PhoneChoice extends StatelessWidget {
  const _PhoneChoice({
    required this.phones,
    required this.selected,
    required this.onSelected,
  });

  final List<String> phones;
  final String selected;
  final ValueChanged<String> onSelected;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          const Text(
            'Which number is the customer?',
            style: EcomsbdType.bodyStrong,
          ),
          const SizedBox(height: 3),
          Text(
            'The message had more than one. Picking the wrong one sends the '
            'parcel to the wrong person, so this is your call.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Wrap(
            spacing: EcomsbdSpacing.xs,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              for (final phone in phones)
                FilterToggle(
                  label: phone,
                  selected: selected == phone,
                  onChanged: (_) => onSelected(phone),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

/// Marks a field the parser was not confident about.
class _CheckThis extends StatelessWidget {
  const _CheckThis({required this.show, required this.child});

  final bool show;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    if (!show) {
      return child;
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        child,
        const Padding(
          padding: EdgeInsets.only(top: 4),
          child: StatusChip(
            label: 'Check this',
            tone: Tone.warning,
            icon: Icons.edit_outlined,
          ),
        ),
      ],
    );
  }
}

class _KnownCustomerNote extends StatelessWidget {
  const _KnownCustomerNote({required this.customer});

  final Customer customer;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(EcomsbdSpacing.sm),
      decoration: BoxDecoration(
        color: customer.isBlocked ? Tone.bad.surface : Tone.info.surface,
        borderRadius: EcomsbdRadii.cardSmall,
      ),
      child: Row(
        children: <Widget>[
          Icon(
            customer.isBlocked ? Icons.block_rounded : Icons.history_rounded,
            size: 16,
            color: customer.isBlocked ? Tone.bad.ink : Tone.info.ink,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              customer.isBlocked
                  ? 'You blocked this customer. ${customer.flagReason ?? ''}'
                  : '${customer.displayName} · ${customer.orderCount} order'
                        '${customer.orderCount == 1 ? '' : 's'} · '
                        '${customer.successRateLabel}',
              style: EcomsbdType.caption.copyWith(
                color: customer.isBlocked ? Tone.bad.ink : Tone.info.ink,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// States plainly that saving does not book anything.
class _NoCourierNote extends StatelessWidget {
  const _NoCourierNote();

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        const Icon(
          Icons.local_shipping_outlined,
          size: 15,
          color: EcomsbdColors.muted2,
        ),
        const SizedBox(width: 6),
        Expanded(
          child: Text(
            'Saving records the order. It does not book a courier — that is a '
            'separate step you take when you are ready to ship.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
          ),
        ),
      ],
    );
  }
}

class _ItemRow extends StatelessWidget {
  const _ItemRow({
    required this.item,
    required this.index,
    required this.canRemove,
    required this.onRemove,
    required this.onChanged,
  });

  final _DraftItem item;
  final int index;
  final bool canRemove;
  final VoidCallback onRemove;
  final VoidCallback onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Row(
          children: <Widget>[
            Expanded(
              child: LabelledField(
                label: 'Item ${index + 1}',
                controller: item.name,
                hint: 'Cotton Abaya XL',
              ),
            ),
            if (canRemove)
              IconButton(
                onPressed: onRemove,
                icon: const Icon(Icons.close_rounded, size: 18),
                tooltip: 'Remove item ${index + 1}',
                color: EcomsbdColors.muted,
              ),
          ],
        ),
        const SizedBox(height: EcomsbdSpacing.xs),
        Row(
          children: <Widget>[
            SizedBox(
              width: 92,
              child: LabelledField(
                label: 'Qty',
                controller: item.quantity,
                keyboardType: TextInputType.number,
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯]')),
                ],
                onChanged: (_) => onChanged(),
              ),
            ),
            const SizedBox(width: EcomsbdSpacing.sm),
            Expanded(
              child: LabelledField(
                label: 'Unit price (৳)',
                controller: item.price,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                inputFormatters: <TextInputFormatter>[
                  FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯.]')),
                ],
                onChanged: (_) => onChanged(),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Re-exported so the orders list can push this screen and read the result.
typedef ComposeResult = SavedOrder;
