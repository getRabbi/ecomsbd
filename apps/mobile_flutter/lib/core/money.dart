/// Money on the client.
///
/// Mirrors `backend/app/common/money.py`. Two rules carry over unchanged:
///
/// * amounts are integer **paisa**, never `double`. The API sends
///   `{"amount_paisa": 125000, "currency": "BDT"}` and this class is the only
///   thing that turns that into text;
/// * display uses Bangladeshi lakh grouping (`৳2,84,500`), with a compact form
///   (`৳2.84L`) for dashboard tiles, matching the UI prototype.
///
/// The client never *computes* money. Totals, profit and settlement come from
/// the server, which owns the ledger (master spec sections 17, 64). What lives
/// here is formatting and the arithmetic needed to render a chart.
library;

import 'package:meta/meta.dart';

const String takaSign = '৳';
const int _paisaPerTaka = 100;

/// An exact amount of money.
@immutable
class Money implements Comparable<Money> {
  const Money(this.paisa, [this.currency = 'BDT']);

  const Money.zero() : paisa = 0, currency = 'BDT';

  /// Parse the wire shape produced by the backend.
  factory Money.fromJson(Map<String, dynamic> json) {
    final raw = json['amount_paisa'];
    if (raw is! int) {
      throw FormatException('amount_paisa must be an integer, got $raw');
    }
    return Money(raw, (json['currency'] as String?) ?? 'BDT');
  }

  final int paisa;
  final String currency;

  bool get isZero => paisa == 0;
  bool get isNegative => paisa < 0;

  Money operator +(Money other) => Money(paisa + other.paisa, currency);
  Money operator -(Money other) => Money(paisa - other.paisa, currency);
  Money operator -() => Money(-paisa, currency);

  @override
  int compareTo(Money other) => paisa.compareTo(other.paisa);

  /// `৳1,250`, `-৳80`, `+৳1,405`.
  ///
  /// A negative amount is always explicit (master spec section 123).
  String format({bool? showPaisa, bool signed = false}) {
    final negative = paisa < 0;
    final magnitude = paisa.abs();
    final whole = magnitude ~/ _paisaPerTaka;
    final fraction = magnitude % _paisaPerTaka;

    final includePaisa = showPaisa ?? (fraction != 0);
    final buffer = StringBuffer(groupBd(whole.toString()));
    if (includePaisa) {
      buffer.write('.${fraction.toString().padLeft(2, '0')}');
    }

    if (negative) {
      return '-$takaSign$buffer';
    }
    return signed ? '+$takaSign$buffer' : '$takaSign$buffer';
  }

  /// `৳87,450` under a lakh, `৳2.84L` above it, `৳1.20Cr` above a crore.
  ///
  /// Truncated rather than rounded, so an abbreviation never claims more money
  /// than the exact figure behind it.
  String formatCompact() {
    final negative = paisa < 0;
    final taka = paisa.abs() ~/ _paisaPerTaka;

    final String body;
    if (taka >= 10000000) {
      body = '$takaSign${_truncate2(taka / 10000000)}Cr';
    } else if (taka >= 100000) {
      body = '$takaSign${_truncate2(taka / 100000)}L';
    } else {
      body = '$takaSign${groupBd(taka.toString())}';
    }
    return negative ? '-$body' : body;
  }

  @override
  String toString() => format();

  @override
  bool operator ==(Object other) =>
      other is Money && other.paisa == paisa && other.currency == currency;

  @override
  int get hashCode => Object.hash(paisa, currency);
}

/// Bangladeshi digit grouping: last three, then pairs.
/// `284500` -> `2,84,500`.
String groupBd(String digits) {
  if (digits.length <= 3) {
    return digits;
  }
  var head = digits.substring(0, digits.length - 3);
  final tail = digits.substring(digits.length - 3);
  final parts = <String>[];
  while (head.length > 2) {
    parts.insert(0, head.substring(head.length - 2));
    head = head.substring(0, head.length - 2);
  }
  if (head.isNotEmpty) {
    parts.insert(0, head);
  }
  parts.add(tail);
  return parts.join(',');
}

String _truncate2(double value) {
  final truncated = (value * 100).floor() / 100;
  return truncated.toStringAsFixed(2);
}

/// Bangla numeral normalisation, mirroring the server's parser.
///
/// Applied before any numeric input is sent, because a seller typing on a
/// Bangla keyboard produces `০১৭…` and the API expects a number it can parse
/// (master spec sections 8, 129).
String normalizeDigits(String input) {
  if (input.isEmpty) {
    return input;
  }
  const bangla = '০১২৩৪৫৬৭৮৯';
  const arabicIndic = '٠١٢٣٤٥٦٧٨٩';
  final buffer = StringBuffer();
  for (final rune in input.runes) {
    final char = String.fromCharCode(rune);
    final banglaIndex = bangla.indexOf(char);
    if (banglaIndex >= 0) {
      buffer.write(banglaIndex);
      continue;
    }
    final arabicIndex = arabicIndic.indexOf(char);
    if (arabicIndex >= 0) {
      buffer.write(arabicIndex);
      continue;
    }
    buffer.write(char);
  }
  return buffer.toString();
}

/// Mask a phone number for display: `01712****78` (master spec section 101).
String maskPhone(String value) {
  final digits = normalizeDigits(value).replaceAll(RegExp(r'\D'), '');
  if (digits.length < 7) {
    return '*' * digits.length;
  }
  final national = digits.startsWith('880')
      ? '0${digits.substring(3)}'
      : digits;
  return '${national.substring(0, 5)}****${national.substring(national.length - 2)}';
}
