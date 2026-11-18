import 'package:ecomsbd/app/providers.dart';
import 'package:ecomsbd/core/api/api_client.dart';
import 'package:ecomsbd/features/orders/order_documents.dart';
import 'package:ecomsbd/l10n/app_locale.dart';
import 'package:ecomsbd/l10n/app_strings.dart';
import 'package:ecomsbd/l10n/documents_strings.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

import '../data/fake_api.dart';
import '../helpers.dart';

typedef _Fake = ({ApiClient client, FakeApiAdapter adapter});

class _RecordingPrinter implements DocumentPrinter {
  final printed = <({String html, String name})>[];

  @override
  Future<void> printHtml(String html, {required String name}) async {
    printed.add((html: html, name: name));
  }
}

const _invoiceHtml = '<!doctype html><title>Invoice</title><p>চালান ORD-7</p>';
const _labelHtml = '<!doctype html><title>Label</title><p>লেবেল ORD-7</p>';

Widget _app(_Fake fake, DocumentPrinter printer, String language) {
  activeAppLocale = language == 'bn' ? AppLocale.bn : AppLocale.en;
  return ProviderScope(
    overrides: [
      apiClientProvider.overrideWithValue(fake.client),
      documentPrinterProvider.overrideWithValue(printer),
    ],
    child: MaterialApp(
      locale: Locale(language),
      supportedLocales: const [Locale('en'), Locale('bn')],
      localizationsDelegates: const [
        AppStrings.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
      ],
      home: const Scaffold(
        body: OrderPrintSheet(orderId: 'o7', orderNumber: 'ORD-7'),
      ),
    ),
  );
}

_Fake _fake() {
  final fake = buildFakeApi();
  fake.adapter.on(
    'GET',
    '/orders/o7/documents/invoice',
    (_) => const FakeReply.html(_invoiceHtml),
  );
  fake.adapter.on(
    'GET',
    '/orders/o7/documents/label',
    (_) => const FakeReply.html(_labelHtml),
  );
  return fake;
}

void main() {
  tearDown(() => activeAppLocale = AppLocale.en);

  test('every English printing string has Bangla', () {
    expect(documentsBn.keys.toSet(), documentsEn.keys.toSet());
    expect(documentsBn.values.where((v) => v.trim().isEmpty), isEmpty);
  });

  for (final language in ['en', 'bn']) {
    testWidgets('prints the invoice HTML as served in $language', (
      tester,
    ) async {
      final fake = _fake();
      final printer = _RecordingPrinter();
      await tester.pumpWidget(_app(fake, printer, language));
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('doc.invoice')));
      await tester.pumpAndSettle();

      expect(printer.printed, hasLength(1));
      expect(printer.printed.single.html, _invoiceHtml);
      expect(printer.printed.single.name, 'ORD-7-invoice');
    });

    testWidgets('fits a 360x800 phone in $language', (tester) async {
      tester.view.physicalSize = referencePhone * 3;
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(_app(_fake(), _RecordingPrinter(), language));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      final strings = language == 'bn' ? documentsBn : documentsEn;
      expect(find.text(strings['doc.printLabel']!), findsOneWidget);
    });
  }

  testWidgets('a label asks for the chosen size and a masked phone', (
    tester,
  ) async {
    final fake = _fake();
    final printer = _RecordingPrinter();
    await tester.pumpWidget(_app(fake, printer, 'en'));
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('doc.size')));
    await tester.pumpAndSettle();
    await tester.tap(find.text(documentsEn['doc.size.a4-8up']!).last);
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('doc.mask')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('doc.label')));
    await tester.pumpAndSettle();

    final request = fake.adapter.to('GET', '/orders/o7/documents/label').single;
    expect(request.query['size'], 'a4-8up');
    expect('${request.query['mask_phone']}', 'true');
    expect(printer.printed.single.html, _labelHtml);
  });

  testWidgets('a shop without the flag is told so and nothing prints', (
    tester,
  ) async {
    final fake = buildFakeApi();
    fake.adapter.on(
      'GET',
      '/orders/o7/documents/invoice',
      (_) => const FakeReply({
        'code': 'FEATURE_DISABLED',
        'message_en': 'Printing documents is not enabled',
        'message_bn': 'এই ফিচারটি এখন বন্ধ আছে।',
        'retryable': false,
      }, statusCode: 403),
    );
    final printer = _RecordingPrinter();
    await tester.pumpWidget(_app(fake, printer, 'en'));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('doc.invoice')));
    await tester.pumpAndSettle();

    expect(find.text(documentsEn['doc.notEnabled']!), findsOneWidget);
    expect(printer.printed, isEmpty);
  });
}
