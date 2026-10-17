import 'package:flutter/services.dart';

/// The one-time code Supabase emails to confirm a new account.
///
/// Sellers are wary of links in email, so sign-up is confirmed by typing the
/// code instead. Supabase alone decides whether a code is right; this only
/// tidies what was typed — Bangla digits become ASCII, spaces and dashes go —
/// so a correct code pasted as "১২৩ ৪৫৬" is not refused for its shape.
abstract final class EmailCode {
  /// Supabase's email OTP length is set per project, between 6 and 10.
  static const int minLength = 6;
  static const int maxLength = 10;

  /// Supabase allows one confirmation email per address per minute.
  static const int resendCooldownSeconds = 60;

  static const String _banglaDigits = '০১২৩৪৫৬৭৮৯';

  /// Digits only, Bangla digits as ASCII, at most [maxLength].
  static String normalize(String raw) {
    final digits = StringBuffer();
    for (final rune in raw.runes) {
      final character = String.fromCharCode(rune);
      final bangla = _banglaDigits.indexOf(character);
      if (bangla >= 0) {
        digits.write(bangla);
      } else if (rune >= 0x30 && rune <= 0x39) {
        digits.write(character);
      }
    }
    final value = digits.toString();
    return value.length > maxLength ? value.substring(0, maxLength) : value;
  }

  /// Whether [code] has a plausible length to send for checking.
  static bool isComplete(String code) {
    final length = normalize(code).length;
    return length >= minLength && length <= maxLength;
  }
}

/// Keeps a code field to [EmailCode.normalize]d digits as the seller types.
class EmailCodeFormatter extends TextInputFormatter {
  const EmailCodeFormatter();

  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) {
    final text = EmailCode.normalize(newValue.text);
    return TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
  }
}
