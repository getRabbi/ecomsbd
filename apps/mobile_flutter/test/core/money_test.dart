import 'package:ecomsbd/core/money.dart';
import 'package:flutter_test/flutter_test.dart';

/// Client money formatting must agree with the server's, character for
/// character. A seller comparing the app to an exported CSV should never see a
/// different number.
void main() {
  group('grouping', () {
    test('uses Bangladeshi lakh grouping', () {
      expect(groupBd('284500'), '2,84,500');
      expect(groupBd('87450'), '87,450');
      expect(groupBd('12345678'), '1,23,45,678');
      expect(groupBd('999'), '999');
    });
  });

  group('format', () {
    test('matches the UI prototype', () {
      expect(const Money(125000).format(), '৳1,250');
      expect(const Money(-8000).format(), '-৳80');
      expect(const Money(140500).format(signed: true), '+৳1,405');
    });

    test('shows paisa only when non-zero', () {
      expect(const Money(125050).format(), '৳1,250.50');
      expect(const Money(125000).format(showPaisa: true), '৳1,250.00');
    });

    test('negative amounts are always explicit', () {
      // Master spec section 123.
      expect(const Money(-424000).format().startsWith('-'), isTrue);
    });
  });

  group('compact', () {
    test('truncates rather than rounding up', () {
      // ৳2,84,500 must never display as ৳2.85L: an abbreviation may not claim
      // more money than the exact figure behind it.
      expect(const Money(28450000).formatCompact(), '৳2.84L');
    });

    test('switches unit at a lakh and a crore', () {
      expect(const Money(8745000).formatCompact(), '৳87,450');
      expect(const Money(28450000).formatCompact(), '৳2.84L');
      expect(const Money(1500000000).formatCompact(), '৳1.50Cr');
    });

    test('keeps the sign', () {
      expect(const Money(-845000).formatCompact(), '-৳8,450');
    });
  });

  group('arithmetic', () {
    test('adds and subtracts exactly', () {
      expect(const Money(1000) + const Money(250), const Money(1250));
      expect(const Money(1000) - const Money(1250), const Money(-250));
    });

    test('parses the wire shape', () {
      final money = Money.fromJson(const <String, dynamic>{
        'amount_paisa': 125000,
        'currency': 'BDT',
      });
      expect(money.paisa, 125000);
      expect(money.currency, 'BDT');
    });

    test('rejects a non-integer amount from the wire', () {
      // A float reaching the client means the server contract broke; failing
      // loudly beats rendering a rounded number.
      expect(
        () => Money.fromJson(const <String, dynamic>{'amount_paisa': 1250.5}),
        throwsFormatException,
      );
    });
  });

  group('numerals', () {
    test('normalises Bangla digits', () {
      expect(normalizeDigits('০১৭১২৩৪৫৬৭৮'), '01712345678');
    });

    test('normalises mixed scripts', () {
      expect(normalizeDigits('০১৭12345678'), '01712345678');
    });

    test('leaves other text alone', () {
      expect(normalizeDigits('Mirpur 10'), 'Mirpur 10');
    });
  });

  group('phone masking', () {
    test('matches the spec format', () {
      expect(maskPhone('01712345678'), '01712****78');
      expect(maskPhone('+8801712345678'), '01712****78');
    });

    test('is idempotent', () {
      expect(maskPhone(maskPhone('01712345678')), '01712****78');
    });

    test('handles Bangla input', () {
      expect(maskPhone('০১৭১২৩৪৫৬৭৮'), '01712****78');
    });
  });
}
