import 'dart:async';

import 'package:app_links/app_links.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

/// Connecting a store or channel from the phone, with no web dashboard.
///
/// WooCommerce's approval page, Shopify's and Facebook's sign-in run in the
/// browser. When the seller finishes, the provider sends them to the API's own
/// return page, which opens `com.ecomsbd.app://integrations/return?...`. That
/// link carries only the connection id and a result code: the app then asks
/// the API what actually happened, so nothing is trusted from the link.

/// The app's URL scheme (Android intent filter, iOS CFBundleURLSchemes).
const String appScheme = 'com.ecomsbd.app';

@immutable
class IntegrationReturn {
  const IntegrationReturn({required this.connectionId, required this.result});

  final String connectionId;

  /// A code from the server's return page, such as `woocommerce`,
  /// `CONNECTED`, `SELECT_PAGE` or `ACCESS_DENIED`. A hint only.
  final String result;

  static IntegrationReturn? fromUri(Uri uri) {
    if (uri.scheme != appScheme || uri.host != 'integrations') return null;
    final id = uri.queryParameters['connection'];
    if (id == null || id.isEmpty) return null;
    return IntegrationReturn(
      connectionId: id,
      result: uri.queryParameters['result'] ?? '',
    );
  }
}

/// Return links as they arrive. Shared by every setup screen.
class IntegrationReturns {
  IntegrationReturns._();

  static final IntegrationReturns instance = IntegrationReturns._();

  final StreamController<IntegrationReturn> _controller =
      StreamController<IntegrationReturn>.broadcast();
  StreamSubscription<Uri>? _links;

  Stream<IntegrationReturn> get stream {
    _start();
    return _controller.stream;
  }

  void _start() {
    if (_links != null) return;
    try {
      _links = AppLinks().uriLinkStream.listen(handle, onError: (_) {});
    } on Object {
      // No link plugin (a test, or a platform without one): the setup screen
      // still checks when the app comes back to the foreground.
    }
  }

  /// Feed one incoming link. Public for the platform listener and tests.
  void handle(Uri uri) {
    final parsed = IntegrationReturn.fromUri(uri);
    if (parsed != null) _controller.add(parsed);
  }
}

final integrationReturnsProvider = Provider<Stream<IntegrationReturn>>(
  (ref) => IntegrationReturns.instance.stream,
);

/// Opens a provider's sign-in page in the phone's browser, where the seller
/// is usually already signed in to their store or Facebook. Replaceable in
/// tests.
typedef ExternalOpener = Future<bool> Function(Uri uri);

final externalOpenerProvider = Provider<ExternalOpener>(
  (ref) =>
      (uri) => launchUrl(uri, mode: LaunchMode.externalApplication),
);
