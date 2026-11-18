import 'package:ecomsbd/core/crash_reporting.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

void main() {
  group('maskPhonesIn', () {
    test('masks every Bangladeshi mobile format', () {
      expect(maskPhonesIn('call 01700000011 now'), 'call 01700****11 now');
      expect(maskPhonesIn('+8801700000012'), '01700****12');
      expect(maskPhonesIn('017-0000-0013'), '01700****13');
      expect(maskPhonesIn('০১৭০০০০০০১৪'), '01700****14');
    });

    test('leaves order numbers and amounts alone', () {
      expect(maskPhonesIn('CP-20261117-0042 ৳1250'), 'CP-20261117-0042 ৳1250');
    });
  });

  group('scrubBreadcrumb', () {
    test('drops console, taps and custom breadcrumbs', () {
      for (final category in <String>['console', 'print', 'ui.click', 'chat']) {
        final crumb = Breadcrumb(
          message: 'customer wrote: আমার অর্ডার 01700000015',
          category: category,
        );
        expect(scrubBreadcrumb(crumb), isNull, reason: category);
      }
      expect(scrubBreadcrumb(null), isNull);
    });

    test('keeps structural breadcrumbs without message text or URLs', () {
      final crumb = scrubBreadcrumb(
        Breadcrumb(
          message: 'GET https://api.example/v1/customers?phone=01700000016',
          category: 'http',
          data: <String, dynamic>{
            'method': 'GET',
            'status_code': 200,
            'url': 'https://api.example/v1/customers?phone=01700000016',
          },
        ),
      );
      expect(crumb, isNotNull);
      expect(crumb!.message, isNull);
      expect(crumb.data, <String, dynamic>{
        'method': 'GET',
        'status_code': 200,
      });
    });
  });

  group('scrubEvent', () {
    test('masks phones, drops params, user detail and request', () {
      final event = SentryEvent(
        message: SentryMessage(
          'lookup failed for 01700000017',
          template: 'lookup failed for %s',
          params: <String>['01700000017'],
        ),
        exceptions: <SentryException>[
          SentryException(type: 'StateError', value: 'no customer 01700000018'),
        ],
        breadcrumbs: <Breadcrumb>[
          Breadcrumb(message: 'typed 01700000019', category: 'console'),
          Breadcrumb(
            category: 'navigation',
            data: <String, dynamic>{'to': '/orders'},
          ),
        ],
        user: SentryUser(
          id: 'u-1',
          email: 'seller@example.com',
          username: 'Rahim',
        ),
        // ignore: deprecated_member_use
        extra: <String, dynamic>{'note': 'call 01700000020'},
      );

      final scrubbed = scrubEvent(event)!;

      expect(scrubbed.message!.formatted, 'lookup failed for 01700****17');
      expect(scrubbed.message!.params, isNull);
      expect(scrubbed.exceptions!.single.value, 'no customer 01700****18');
      expect(scrubbed.breadcrumbs!.map((c) => c.category), <String?>[
        'navigation',
      ]);
      expect(scrubbed.user!.id, 'u-1');
      expect(scrubbed.user!.email, isNull);
      expect(scrubbed.user!.username, isNull);
      expect(scrubbed.request, isNull);
      // ignore: deprecated_member_use
      expect(scrubbed.extra, <String, dynamic>{'note': 'call 01700****20'});
    });
  });

  group('configureSentry', () {
    test('never sends default PII, screenshots or the view tree', () {
      final options = SentryFlutterOptions();
      configureSentry(options, 'https://public@example.invalid/1');

      expect(options.sendDefaultPii, isFalse);
      expect(options.attachScreenshot, isFalse);
      expect(options.attachViewHierarchy, isFalse);
      expect(options.beforeSend, isNotNull);
      expect(options.beforeBreadcrumb, isNotNull);
    });
  });

  test('without a DSN the app runs and Sentry is not started', () async {
    var ran = false;
    await runWithCrashReporting(() => ran = true, dsn: '');
    expect(ran, isTrue);
    expect(Sentry.isEnabled, isFalse);
  });
}
