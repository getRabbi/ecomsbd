/// Pay by bKash / Nagad with a TrxID (V4 WP2.5).
///
/// The server decides whether the form is offered
/// (`GET /v1/billing/manual-payments/options`), quotes every amount and
/// re-checks everything on submit. A store build never gets the form: the API
/// client tells the server it is one (`Env.directBuild`, master spec §27.1).
library;

import '../../core/money.dart';

class ReceivingAccount {
  const ReceivingAccount({
    required this.method,
    required this.number,
    required this.label,
  });

  factory ReceivingAccount.fromJson(Map<String, dynamic> json) =>
      ReceivingAccount(
        method: json['method'] as String,
        number: json['number'] as String,
        label: json['label'] as String? ?? '',
      );

  final String method;
  final String number;
  final String label;
}

class PaymentQuote {
  const PaymentQuote({required this.months, required this.amount});

  factory PaymentQuote.fromJson(Map<String, dynamic> json) => PaymentQuote(
    months: json['months'] as int,
    amount: Money.fromJson(json['amount'] as Map<String, dynamic>),
  );

  final int months;
  final Money amount;
}

class PayablePlan {
  const PayablePlan({
    required this.code,
    required this.name,
    required this.monthlyPrice,
    required this.amounts,
  });

  factory PayablePlan.fromJson(Map<String, dynamic> json) => PayablePlan(
    code: json['code'] as String,
    name: json['name'] as String,
    monthlyPrice: Money.fromJson(json['monthly_price'] as Map<String, dynamic>),
    amounts: <PaymentQuote>[
      for (final row in json['amounts'] as List<dynamic>? ?? const <dynamic>[])
        PaymentQuote.fromJson(row as Map<String, dynamic>),
    ],
  );

  final String code;
  final String name;
  final Money monthlyPrice;
  final List<PaymentQuote> amounts;

  /// The server's exact amount, or null when it quoted none for [months].
  Money? amountFor(int months) {
    for (final quote in amounts) {
      if (quote.months == months) return quote.amount;
    }
    return null;
  }
}

class ManualPaymentOptions {
  const ManualPaymentOptions({
    required this.available,
    required this.unavailableReason,
    required this.accounts,
    required this.plans,
  });

  factory ManualPaymentOptions.fromJson(
    Map<String, dynamic> json,
  ) => ManualPaymentOptions(
    available: json['available'] as bool? ?? false,
    unavailableReason: json['unavailable_reason'] as String?,
    accounts: <ReceivingAccount>[
      for (final row in json['accounts'] as List<dynamic>? ?? const <dynamic>[])
        ReceivingAccount.fromJson(row as Map<String, dynamic>),
    ],
    plans: <PayablePlan>[
      for (final row in json['plans'] as List<dynamic>? ?? const <dynamic>[])
        PayablePlan.fromJson(row as Map<String, dynamic>),
    ],
  );

  final bool available;
  final String? unavailableReason;
  final List<ReceivingAccount> accounts;
  final List<PayablePlan> plans;

  List<String> get methods =>
      <String>{for (final account in accounts) account.method}.toList();
}

class ManualPaymentSubmission {
  const ManualPaymentSubmission({
    required this.id,
    required this.plan,
    required this.months,
    required this.amount,
    required this.method,
    required this.payerNumberMasked,
    required this.trxId,
    required this.state,
    this.reason,
    this.periodEnd,
  });

  factory ManualPaymentSubmission.fromJson(Map<String, dynamic> json) =>
      ManualPaymentSubmission(
        id: json['id'] as String,
        plan: json['plan'] as String,
        months: json['months'] as int,
        amount: Money.fromJson(json['amount'] as Map<String, dynamic>),
        method: json['method'] as String,
        payerNumberMasked: json['payer_number_masked'] as String,
        trxId: json['trx_id'] as String,
        state: json['state'] as String,
        reason: json['reason'] as String?,
        periodEnd: json['period_end'] == null
            ? null
            : DateTime.parse(json['period_end'] as String).toLocal(),
      );

  final String id;
  final String plan;
  final int months;
  final Money amount;
  final String method;
  final String payerNumberMasked;
  final String trxId;
  final String state;
  final String? reason;
  final DateTime? periodEnd;
}

/// What the server will store: upper-case, Bangla digits folded, no spaces
/// or dashes.
String cleanTrxId(String raw) =>
    normalizeDigits(raw).replaceAll(RegExp(r'[\s-]+'), '').toUpperCase();

bool isTrxId(String raw) =>
    RegExp(r'^[A-Z0-9]{6,20}$').hasMatch(cleanTrxId(raw));

bool isBdMobile(String raw) => RegExp(
  r'^(?:\+?88)?01[3-9]\d{8}$',
).hasMatch(normalizeDigits(raw).replaceAll(RegExp(r'[\s-]'), ''));

/// The copy key for a refused submit, from the API's `details.code`.
String? manualPaymentRefusalKey(Map<String, dynamic>? details) =>
    switch (details?['code']) {
      'TRX_ID_ALREADY_USED' => 'mp.errUsed',
      'TRX_ID_INVALID' => 'mp.errTrx',
      'MANUAL_PAYMENT_UNAVAILABLE' => 'mp.errUnavailable',
      _ => null,
    };

String manualPaymentMethodKey(String method) =>
    method == 'NAGAD' ? 'mp.nagad' : 'mp.bkash';
