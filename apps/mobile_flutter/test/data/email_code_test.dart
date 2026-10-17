import 'package:ecomsbd/data/auth/email_code.dart';
import 'package:flutter_test/flutter_test.dart';

/// Sign-up is confirmed with a code typed from the email, not a link.
void main() {
  test('a code is digits only; Bangla digits, spaces and dashes are kept', () {
    expect(EmailCode.normalize('123456'), '123456');
    expect(EmailCode.normalize('১২৩৪৫৬'), '123456');
    expect(EmailCode.normalize(' 123-456 '), '123456');
    expect(EmailCode.normalize('১২৩ ৪৫৬'), '123456');
    expect(EmailCode.normalize('abc'), '');
    expect(EmailCode.normalize('1' * 20).length, EmailCode.maxLength);
  });

  test('only a plausible length is sent for checking', () {
    expect(EmailCode.isComplete('12345'), isFalse);
    expect(EmailCode.isComplete('123456'), isTrue);
    expect(EmailCode.isComplete('১২৩৪৫৬৭৮'), isTrue);
    expect(EmailCode.isComplete(''), isFalse);
  });

  test('the field keeps typed and pasted codes to digits', () {
    const formatter = EmailCodeFormatter();
    final value = formatter.formatEditUpdate(
      TextEditingValue.empty,
      const TextEditingValue(text: '১২৩-৪৫৬'),
    );
    expect(value.text, '123456');
    expect(value.selection.baseOffset, 6);
  });
}
