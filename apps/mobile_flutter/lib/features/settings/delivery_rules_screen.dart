import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// Shop delivery rules (V4 WP0.4): zone charges, default courier, COD and
/// call thresholds. `GET/PATCH /shop/delivery-rules`; the server answers
/// FEATURE_DISABLED while the shop's flag is off, and the screen says so.
final deliveryRulesProvider = FutureProvider.autoDispose<Map<String, dynamic>>(
  (ref) => ref.watch(apiClientProvider).get('/shop/delivery-rules'),
);

/// Couriers a shop can pick as default. Keys are the server's provider keys.
const deliveryRuleCouriers = <String, String>{
  'steadfast': 'Steadfast',
  'pathao': 'Pathao',
  'redx': 'RedX',
};

/// Taka text to paisa without a `double` (see ProductFormScreen._paisa).
int? takaToPaisa(String raw) {
  final text = normalizeDigits(raw).replaceAll(',', '').trim();
  final match = RegExp(r'^(\d+)(?:\.(\d{1,2}))?$').firstMatch(text);
  if (match == null) {
    return null;
  }
  final fraction = (match.group(2) ?? '').padRight(2, '0');
  return int.parse(match.group(1)!) * 100 + int.parse(fraction);
}

String paisaToTaka(int paisa) {
  final taka = paisa ~/ 100;
  final rest = paisa % 100;
  return rest == 0 ? '$taka' : '$taka.${rest.toString().padLeft(2, '0')}';
}

class DeliveryRulesScreen extends ConsumerWidget {
  const DeliveryRulesScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rules = ref.watch(deliveryRulesProvider);
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('dr.title'))),
      body: rules.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (error, _) {
          final bn = context.strings.locale == AppLocale.bn;
          final message =
              error is ApiError && error.code == ApiErrorCode.featureDisabled
              ? context.tr('dr.notEnabled')
              : error is ApiError
              ? (bn ? error.messageBn : error.messageEn)
              : context.tr('dr.loadFailed');
          return Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text(message, textAlign: TextAlign.center),
            ),
          );
        },
        data: (values) => _DeliveryRulesForm(initial: values),
      ),
    );
  }
}

class _DeliveryRulesForm extends ConsumerStatefulWidget {
  const _DeliveryRulesForm({required this.initial});

  final Map<String, dynamic> initial;

  @override
  ConsumerState<_DeliveryRulesForm> createState() => _DeliveryRulesFormState();
}

enum _Kind { money, count, percent, time }

class _Field {
  const _Field(this.key, this.label, this.kind);
  final String key;
  final String label;
  final _Kind kind;
}

const _chargeFields = <_Field>[
  _Field('inside_dhaka_charge_paisa', 'dr.inside', _Kind.money),
  _Field('dhaka_suburb_charge_paisa', 'dr.suburb', _Kind.money),
  _Field('outside_dhaka_charge_paisa', 'dr.outside', _Kind.money),
];
const _autoFields = <_Field>[
  _Field('autopilot_daily_cap', 'dr.dailyCap', _Kind.count),
  _Field('quiet_start', 'dr.quietStart', _Kind.time),
  _Field('quiet_end', 'dr.quietEnd', _Kind.time),
];
const _callFields = <_Field>[
  _Field('max_auto_cod_paisa', 'dr.maxAutoCod', _Kind.money),
];
const _riskFields = <_Field>[
  _Field('risk_low_bps', 'dr.riskLow', _Kind.percent),
  _Field('trusted_network_bps', 'dr.trustedNetwork', _Kind.percent),
  _Field('trusted_repeat_min', 'dr.trustedRepeat', _Kind.count),
];
const _allFields = <_Field>[
  ..._chargeFields,
  ..._autoFields,
  ..._callFields,
  ..._riskFields,
];

final _timePattern = RegExp(r'^(?:[01]\d|2[0-3]):[0-5]\d$');

class _DeliveryRulesFormState extends ConsumerState<_DeliveryRulesForm> {
  final _formKey = GlobalKey<FormState>();
  late final Map<String, TextEditingController> _controllers;
  String? _courier;
  bool _callEveryone = false;
  bool _callNobody = false;
  bool _saving = false;

  @override
  void initState() {
    super.initState();
    final initial = widget.initial;
    _controllers = {
      for (final field in _allFields)
        field.key: TextEditingController(text: _display(field, initial)),
    };
    _courier = initial['default_courier'] as String?;
    _callEveryone = initial['call_everyone'] == true;
    _callNobody = initial['call_nobody'] == true;
  }

  static String _display(_Field field, Map<String, dynamic> values) {
    final raw = values[field.key];
    return switch (field.kind) {
      _Kind.money => paisaToTaka((raw as num?)?.toInt() ?? 0),
      _Kind.percent => '${((raw as num?)?.toInt() ?? 0) ~/ 100}',
      _Kind.count => '${(raw as num?)?.toInt() ?? 0}',
      _Kind.time => '${raw ?? ''}',
    };
  }

  /// The value to send, or null when the text is not valid for the field.
  static Object? _parse(_Field field, String text) {
    final cleaned = normalizeDigits(text).trim();
    switch (field.kind) {
      case _Kind.money:
        return takaToPaisa(cleaned);
      case _Kind.count:
        return RegExp(r'^\d{1,5}$').hasMatch(cleaned)
            ? int.parse(cleaned)
            : null;
      case _Kind.percent:
        if (!RegExp(r'^\d{1,3}$').hasMatch(cleaned)) {
          return null;
        }
        final percent = int.parse(cleaned);
        return percent <= 100 ? percent * 100 : null;
      case _Kind.time:
        return _timePattern.hasMatch(cleaned) ? cleaned : null;
    }
  }

  static String _errorKey(_Kind kind) => switch (kind) {
    _Kind.money => 'dr.invalidMoney',
    _Kind.count => 'dr.invalidNumber',
    _Kind.percent => 'dr.invalidPercent',
    _Kind.time => 'dr.invalidTime',
  };

  @override
  void dispose() {
    for (final controller in _controllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    final body = <String, Object?>{
      for (final field in _allFields)
        field.key: _parse(field, _controllers[field.key]!.text),
      'default_courier': _courier,
      'call_everyone': _callEveryone,
      'call_nobody': _callNobody,
    };
    setState(() => _saving = true);
    final messenger = ScaffoldMessenger.of(context);
    final bn = context.strings.locale == AppLocale.bn;
    final saved = context.tr('dr.saved');
    try {
      await ref
          .read(apiClientProvider)
          .patch('/shop/delivery-rules', body: body);
      ref.invalidate(deliveryRulesProvider);
      messenger.showSnackBar(SnackBar(content: Text(saved)));
    } on ApiError catch (error) {
      messenger.showSnackBar(
        SnackBar(content: Text(bn ? error.messageBn : error.messageEn)),
      );
    } finally {
      if (mounted) {
        setState(() => _saving = false);
      }
    }
  }

  Widget _input(_Field field) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextFormField(
        key: ValueKey('dr.${field.key}'),
        controller: _controllers[field.key],
        keyboardType: field.kind == _Kind.time
            ? TextInputType.datetime
            : const TextInputType.numberWithOptions(decimal: true),
        decoration: InputDecoration(labelText: context.tr(field.label)),
        validator: (text) => _parse(field, text ?? '') == null
            ? context.tr(_errorKey(field.kind))
            : null,
      ),
    );
  }

  Widget _section(String key) => Padding(
    padding: const EdgeInsets.only(top: 16, bottom: 8),
    child: Text(
      context.tr(key),
      style: Theme.of(context).textTheme.titleMedium,
    ),
  );

  @override
  Widget build(BuildContext context) {
    final configured = widget.initial['configured'] == true;
    return Form(
      key: _formKey,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
        children: [
          if (!configured)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Text(context.tr('dr.defaultsNote')),
              ),
            ),
          _section('dr.sectionCharges'),
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Text(
              context.tr('dr.chargesNote'),
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          ..._chargeFields.map(_input),
          _section('dr.sectionCourier'),
          DropdownButtonFormField<String?>(
            key: const ValueKey('dr.default_courier'),
            initialValue: _courier,
            isExpanded: true,
            decoration: InputDecoration(
              labelText: context.tr('dr.defaultCourier'),
            ),
            items: [
              DropdownMenuItem<String?>(
                value: null,
                child: Text(context.tr('dr.courierNone')),
              ),
              for (final entry in deliveryRuleCouriers.entries)
                DropdownMenuItem<String?>(
                  value: entry.key,
                  child: Text(entry.value),
                ),
            ],
            onChanged: (value) => setState(() => _courier = value),
          ),
          _section('dr.sectionCalls'),
          ..._callFields.map(_input),
          SwitchListTile(
            key: const ValueKey('dr.call_everyone'),
            contentPadding: EdgeInsets.zero,
            title: Text(context.tr('dr.callEveryone')),
            value: _callEveryone,
            onChanged: (on) => setState(() {
              _callEveryone = on;
              if (on) {
                _callNobody = false;
              }
            }),
          ),
          SwitchListTile(
            key: const ValueKey('dr.call_nobody'),
            contentPadding: EdgeInsets.zero,
            title: Text(context.tr('dr.callNobody')),
            value: _callNobody,
            onChanged: (on) => setState(() {
              _callNobody = on;
              if (on) {
                _callEveryone = false;
              }
            }),
          ),
          _section('dr.sectionAuto'),
          ..._autoFields.map(_input),
          _section('dr.sectionRisk'),
          ..._riskFields.map(_input),
          const SizedBox(height: 16),
          FilledButton(
            key: const ValueKey('dr.save'),
            onPressed: _saving ? null : _save,
            child: Text(context.tr('dr.save')),
          ),
        ],
      ),
    );
  }
}
