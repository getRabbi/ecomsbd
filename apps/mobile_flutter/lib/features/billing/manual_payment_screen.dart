import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/billing/billing_providers.dart';
import '../../data/billing/manual_payment.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import 'plans_screen.dart' show formatDay;

/// Pay by bKash or Nagad with a TrxID (V4 WP2.5).
///
/// Everything shown comes from the server: whether the form is offered, the
/// receiving numbers and the exact amount for each choice. Submitting records
/// a claim; the plan turns on only after an operator finds the TrxID.
class ManualPaymentScreen extends ConsumerWidget {
  const ManualPaymentScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final options = ref.watch(manualPaymentOptionsProvider);
    return DetailScaffold(
      title: context.tr('mp.title'),
      children: <Widget>[
        options.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (error, _) => ErrorStateCard(
            error: error is ApiError ? error : ApiError.unexpected(error),
            onRetry: () => ref.invalidate(manualPaymentOptionsProvider),
          ),
          data: (data) => data.available
              ? _PaymentForm(options: data)
              : Text(context.tr('mp.errUnavailable')),
        ),
        if (options.valueOrNull?.available ?? false) const _History(),
      ],
    );
  }
}

class _PaymentForm extends ConsumerStatefulWidget {
  const _PaymentForm({required this.options});

  final ManualPaymentOptions options;

  @override
  ConsumerState<_PaymentForm> createState() => _PaymentFormState();
}

class _PaymentFormState extends ConsumerState<_PaymentForm> {
  final _payer = TextEditingController();
  final _trx = TextEditingController();
  late String _plan;
  late int _months;
  late String _method;
  bool _busy = false;
  bool _showErrors = false;
  String? _refusal;
  bool _sent = false;

  @override
  void initState() {
    super.initState();
    final options = widget.options;
    _plan = options.plans.isEmpty ? '' : options.plans.first.code;
    _months = _choices.isEmpty ? 1 : _choices.first;
    _method = options.methods.isEmpty ? '' : options.methods.first;
  }

  @override
  void dispose() {
    _payer.dispose();
    _trx.dispose();
    super.dispose();
  }

  PayablePlan? get _selected {
    for (final plan in widget.options.plans) {
      if (plan.code == _plan) return plan;
    }
    return null;
  }

  List<int> get _choices => <int>[
    for (final quote in _selected?.amounts ?? const <PaymentQuote>[])
      quote.months,
  ];

  bool get _payerOk => isBdMobile(_payer.text);
  bool get _trxOk => isTrxId(_trx.text);

  Future<void> _submit() async {
    setState(() {
      _showErrors = true;
      _refusal = null;
      _sent = false;
    });
    if (!_payerOk || !_trxOk || _selected?.amountFor(_months) == null) return;
    setState(() => _busy = true);
    try {
      await ref
          .read(billingRepositoryProvider)
          .submitManualPayment(
            plan: _plan,
            months: _months,
            method: _method,
            payerNumber: _payer.text,
            trxId: _trx.text,
          );
      if (!mounted) return;
      _payer.clear();
      _trx.clear();
      setState(() {
        _sent = true;
        _showErrors = false;
      });
      ref.invalidate(manualPaymentsProvider);
    } on ApiError catch (error) {
      if (!mounted) return;
      setState(
        () => _refusal = manualPaymentRefusalKey(error.details) == null
            ? error.displayMessage
            : context.tr(manualPaymentRefusalKey(error.details)!),
      );
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _chips<T>(
    String label,
    List<T> values,
    T selected,
    String Function(T) text,
    void Function(T) onSelected,
    String keyPrefix, {
    String Function(T)? id,
  }) {
    return Padding(
      padding: const EdgeInsets.only(top: EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(label, style: EcomsbdType.caption),
          const SizedBox(height: EcomsbdSpacing.xs),
          Wrap(
            spacing: EcomsbdSpacing.sm,
            runSpacing: EcomsbdSpacing.xs,
            children: <Widget>[
              for (final value in values)
                ChoiceChip(
                  key: ValueKey('$keyPrefix.${id?.call(value) ?? value}'),
                  label: Text(text(value)),
                  selected: value == selected,
                  onSelected: (_) => setState(() => onSelected(value)),
                ),
            ],
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final options = widget.options;
    final amount = _selected?.amountFor(_months);
    final accounts = options.accounts.where((a) => a.method == _method);

    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('mp.intro'), style: EcomsbdType.caption),
          _chips<PayablePlan>(
            context.tr('mp.plan'),
            options.plans,
            _selected ?? options.plans.first,
            (plan) => '${plan.name} · ${plan.monthlyPrice.format()}',
            (plan) {
              _plan = plan.code;
              if (!_choices.contains(_months) && _choices.isNotEmpty) {
                _months = _choices.first;
              }
            },
            'mp.plan',
            id: (plan) => plan.code,
          ),
          _chips<int>(
            context.tr('mp.months'),
            _choices,
            _months,
            (months) => context.tr('mp.monthsValue', {'count': months}),
            (months) => _months = months,
            'mp.months',
          ),
          _chips<String>(
            context.tr('mp.method'),
            options.methods,
            _method,
            (method) => context.tr(manualPaymentMethodKey(method)),
            (method) => _method = method,
            'mp.method',
          ),
          const SizedBox(height: EcomsbdSpacing.md),
          Text(context.tr('mp.amount'), style: EcomsbdType.caption),
          Text(
            amount?.format() ?? '—',
            key: const ValueKey('mp.amount'),
            style: EcomsbdType.pageTitle,
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(context.tr('mp.sendTo'), style: EcomsbdType.caption),
          for (final account in accounts)
            SelectableText(
              account.label.isEmpty
                  ? account.number
                  : '${account.number} · ${account.label}',
              style: EcomsbdType.bodyStrong,
            ),
          const SizedBox(height: EcomsbdSpacing.md),
          TextField(
            key: const ValueKey('mp.payer'),
            controller: _payer,
            keyboardType: TextInputType.phone,
            decoration: InputDecoration(
              labelText: context.tr('mp.payer'),
              errorText: _showErrors && !_payerOk
                  ? context.tr('mp.invalidPayer')
                  : null,
            ),
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          TextField(
            key: const ValueKey('mp.trx'),
            controller: _trx,
            textCapitalization: TextCapitalization.characters,
            autocorrect: false,
            decoration: InputDecoration(
              labelText: context.tr('mp.trx'),
              helperText: context.tr('mp.trxHint'),
              helperMaxLines: 3,
              errorText: _showErrors && !_trxOk
                  ? context.tr('mp.invalidTrx')
                  : null,
            ),
          ),
          if (_refusal != null)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.sm),
              child: Text(
                _refusal!,
                key: const ValueKey('mp.refusal'),
                style: EcomsbdType.caption.copyWith(color: Tone.bad.ink),
              ),
            ),
          if (_sent)
            Padding(
              padding: const EdgeInsets.only(top: EcomsbdSpacing.sm),
              child: Text(
                context.tr('mp.sent'),
                style: EcomsbdType.caption.copyWith(color: Tone.good.ink),
              ),
            ),
          const SizedBox(height: EcomsbdSpacing.md),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              key: const ValueKey('mp.submit'),
              onPressed: _busy ? null : _submit,
              child: Text(
                _busy ? context.tr('mp.sending') : context.tr('mp.submit'),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _History extends ConsumerWidget {
  const _History();

  static Tone _tone(String state) => switch (state) {
    'APPROVED' => Tone.good,
    'REJECTED' => Tone.bad,
    _ => Tone.warning,
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rows = ref.watch(manualPaymentsProvider).valueOrNull ?? const [];
    if (rows.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        SectionHeader(title: context.tr('mp.history')),
        for (final row in rows)
          GlassCard(
            margin: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Row(
                  children: <Widget>[
                    Expanded(
                      child: Text(
                        '${row.amount.format()} · '
                        '${context.tr(manualPaymentMethodKey(row.method))} · '
                        '${row.trxId}',
                        style: EcomsbdType.bodyStrong,
                      ),
                    ),
                    StatusChip(
                      label: context.tr('mp.state.${row.state}'),
                      tone: _tone(row.state),
                    ),
                  ],
                ),
                if (row.state == 'APPROVED' && row.periodEnd != null)
                  Text(
                    context.tr('mp.until', {'date': formatDay(row.periodEnd!)}),
                    style: EcomsbdType.caption,
                  ),
                if (row.state == 'REJECTED' && row.reason != null)
                  Text(
                    context.tr('mp.why', {'reason': row.reason}),
                    style: EcomsbdType.caption,
                  ),
              ],
            ),
          ),
      ],
    );
  }
}
