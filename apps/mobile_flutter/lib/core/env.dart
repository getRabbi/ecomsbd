/// Build-time configuration.
///
/// Supplied with `--dart-define`, never read from a file the app ships, so a
/// debug build cannot accidentally point a release at a development server:
///
/// ```
/// flutter run --dart-define=ECOMSBD_API_BASE_URL=http://10.0.2.2:8000
/// ```
library;

import 'package:flutter/foundation.dart';

class Env {
  const Env._();

  /// Base URL of the ecomsbd API.
  ///
  /// The default is the Android emulator's alias for the host machine's
  /// loopback, which is what a developer running the backend locally needs.
  static const String apiBaseUrl = String.fromEnvironment(
    'ECOMSBD_API_BASE_URL',
    defaultValue: 'http://10.0.2.2:8000',
  );

  static const String apiVersionPrefix = '/v1';

  /// Sentry DSN. Empty disables reporting.
  static const String sentryDsn = String.fromEnvironment('ECOMSBD_SENTRY_DSN');

  /// Displayed product name. Locked.
  static const String appName = 'ecomsbd';

  static const Duration connectTimeout = Duration(seconds: 12);
  static const Duration receiveTimeout = Duration(seconds: 25);

  /// Whether the API is reached over plain HTTP.
  ///
  /// True only for local development against an emulator host. The Android
  /// manifest sets `usesCleartextTraffic="false"`, so a release build cannot
  /// talk to an http:// endpoint even if one is configured.
  static bool get isInsecureEndpoint => apiBaseUrl.startsWith('http://');

  static bool get isDebugBuild => kDebugMode;
}
