import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:printing/printing.dart';

import '../../app/providers.dart';
import '../../core/api/api_error.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// Invoices and labels (V4 WP0.5) are HTML the API renders. The phone's print
/// system turns them into a PDF (Android WebView print shapes Bangla
/// correctly), and the seller prints or shares it from the system sheet.
abstract interface class DocumentPrinter {
  Future<void> printHtml(String html, {required String name});
}

class SystemDocumentPrinter implements DocumentPrinter {
  const SystemDocumentPrinter();

  @override
  Future<void> printHtml(String html, {required String name}) async {
    await Printing.layoutPdf(
      name: name,
      // Deprecated upstream because the output depends on the platform's
      // WebView, which is exactly why it is used: V4 WP0.5 needs the OS
      // renderer to shape Bangla; PDF-drawing APIs do not.
      // ignore: deprecated_member_use
      onLayout: (format) => Printing.convertHtml(format: format, html: html),
    );
  }
}

final documentPrinterProvider = Provider<DocumentPrinter>(
  (ref) => const SystemDocumentPrinter(),
);

const orderLabelSizes = <String>['4x6', '3x4', 'a4-8up'];

Future<void> showOrderPrintSheet(
  BuildContext context, {
  required String orderId,
  required String orderNumber,
}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    builder: (_) => OrderPrintSheet(orderId: orderId, orderNumber: orderNumber),
  );
}

class OrderPrintSheet extends ConsumerStatefulWidget {
  const OrderPrintSheet({
    super.key,
    required this.orderId,
    required this.orderNumber,
  });

  final String orderId;
  final String orderNumber;

  @override
  ConsumerState<OrderPrintSheet> createState() => _OrderPrintSheetState();
}

class _OrderPrintSheetState extends ConsumerState<OrderPrintSheet> {
  String _size = orderLabelSizes.first;
  bool _maskPhone = false;
  bool _busy = false;
  String? _error;

  Future<void> _print(String kind) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    final bn = context.strings.locale == AppLocale.bn;
    final notEnabled = context.tr('doc.notEnabled');
    final failed = context.tr('doc.failed');
    try {
      final api = ref.read(apiClientProvider);
      final html = kind == 'invoice'
          ? await api.getText('/orders/${widget.orderId}/documents/invoice')
          : await api.getText(
              '/orders/${widget.orderId}/documents/label',
              query: <String, dynamic>{'size': _size, 'mask_phone': _maskPhone},
            );
      await ref
          .read(documentPrinterProvider)
          .printHtml(html, name: '${widget.orderNumber}-$kind');
    } on ApiError catch (error) {
      setState(
        () => _error = error.code == ApiErrorCode.featureDisabled
            ? notEnabled
            : (bn ? error.messageBn : error.messageEn),
      );
    } on Exception {
      setState(() => _error = failed);
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text(
              context.tr('doc.printTitle'),
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            if (_error != null) ...<Widget>[
              Text(
                _error!,
                key: const ValueKey('doc.error'),
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
              const SizedBox(height: 8),
            ],
            FilledButton.icon(
              key: const ValueKey('doc.invoice'),
              onPressed: _busy ? null : () => _print('invoice'),
              icon: const Icon(Icons.receipt_long_outlined),
              label: Text(context.tr('doc.printInvoice')),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              key: const ValueKey('doc.size'),
              initialValue: _size,
              isExpanded: true,
              decoration: InputDecoration(
                labelText: context.tr('doc.labelSize'),
              ),
              items: <DropdownMenuItem<String>>[
                for (final size in orderLabelSizes)
                  DropdownMenuItem<String>(
                    value: size,
                    child: Text(context.tr('doc.size.$size')),
                  ),
              ],
              onChanged: (value) =>
                  setState(() => _size = value ?? orderLabelSizes.first),
            ),
            SwitchListTile(
              key: const ValueKey('doc.mask'),
              contentPadding: EdgeInsets.zero,
              title: Text(context.tr('doc.maskPhone')),
              subtitle: Text(context.tr('doc.maskPhoneHint')),
              value: _maskPhone,
              onChanged: (on) => setState(() => _maskPhone = on),
            ),
            OutlinedButton.icon(
              key: const ValueKey('doc.label'),
              onPressed: _busy ? null : () => _print('label'),
              icon: const Icon(Icons.local_shipping_outlined),
              label: Text(context.tr('doc.printLabel')),
            ),
          ],
        ),
      ),
    );
  }
}
