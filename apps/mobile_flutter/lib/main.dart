import 'dart:async';

import 'package:device_info_plus/device_info_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

import 'app/app.dart';
import 'l10n/app_locale.dart';
import 'data/auth/supabase_bootstrap.dart';
import 'data/notifications/push_registration.dart';
import 'app/providers.dart';
import 'design/glass.dart';
import 'design/theme.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await initializeSupabaseAuth();
  await initializePush();

  // Portrait only: every screen in the locked design is a single-column
  // seller-operations layout, and landscape would give it nothing.
  await SystemChrome.setPreferredOrientations(<DeviceOrientation>[
    DeviceOrientation.portraitUp,
  ]);
  SystemChrome.setSystemUIOverlayStyle(ecomsbdLightOverlay);

  // Read before the container is built so the first frame is already in the
  // seller's language instead of flipping to it a frame later.
  final savedLocale = await loadSavedLocale();

  final container = ProviderContainer(
    overrides: <Override>[
      localeProvider.overrideWith((ref) => LocaleController(savedLocale)),
    ],
  );
  await _bootstrap(container);

  runApp(
    UncontrolledProviderScope(container: container, child: const EcomsbdApp()),
  );
}

/// Startup work that must finish before the first frame.
///
/// Kept deliberately small: the splash screen is what the seller sees while
/// this runs, and on the low-end devices this app targets every millisecond
/// here is visible.
Future<void> _bootstrap(ProviderContainer container) async {
  // Resolve metadata before constructing repositories which watch appVersion.
  // Otherwise that update disposes the new auth/deep-link subscription.
  await _detectDeviceTier(container);
  await _readAppVersion(container);
  container.read(authControllerProvider);
  await container.read(authRepositoryProvider).startAuthLinks();

  // Restoring the session is not awaited: the router shows the splash screen
  // while it runs and moves on by itself. Awaiting it would hold a blank frame
  // for the length of a network round trip on a slow connection.
  unawaited(container.read(authControllerProvider.notifier).restore());
}

/// Decide whether this device can afford live backdrop blur.
///
/// Master spec section 53: the app targets low-end Android. `BackdropFilter` is
/// the most expensive thing in this design, so on a device with little RAM or
/// an older Android release the glass switches to its opaque fallback, which
/// looks nearly identical and costs a fraction as much per frame.
Future<void> _detectDeviceTier(ProviderContainer container) async {
  if (!defaultTargetPlatform.isAndroid) {
    return;
  }
  try {
    final info = await DeviceInfoPlugin().androidInfo;
    // Android 9 and below covers the older, slower devices still common in
    // this market; below 3GB of RAM is where dropped frames become obvious.
    final isLowEnd =
        info.version.sdkInt <= 28 ||
        (info.systemFeatures.contains('android.hardware.ram.low'));
    if (isLowEnd) {
      container.read(isLowEndDeviceProvider.notifier).state = true;
      container.read(effectsModeProvider.notifier).state = EffectsMode.reduced;
    }
  } on Object {
    // Device metadata is best-effort; failing to read it must not stop launch.
  }
}

Future<void> _readAppVersion(ProviderContainer container) async {
  try {
    final info = await PackageInfo.fromPlatform();
    container.read(appVersionProvider.notifier).state =
        '${info.version}+${info.buildNumber}';
  } on Object {
    // Reported to the server as a diagnostic header only.
  }
}

extension on TargetPlatform {
  bool get isAndroid => this == TargetPlatform.android;
}
