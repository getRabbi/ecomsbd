// Crash reporting (V4 WP0.2).
//
// Sentry starts only when ECOMSBD_SENTRY_DSN is set at build time. Nothing
// personal leaves the device: sendDefaultPii is off, phone numbers are masked
// with the same helper the UI uses, and breadcrumbs that could carry message
// text (console output, taps, free-form messages) are dropped.

import 'dart:async';

import 'package:sentry_flutter/sentry_flutter.dart';

import 'env.dart';
import 'money.dart';

/// Bangladeshi mobile numbers in Latin or Bangla digits, with or without
/// `+88`/`88` and spaces or dashes between digits.
final RegExp _phonePattern = RegExp(
  r'(?:\+?(?:88|৮৮)[\s-]?)?[0০]?[1১][3-9৩-৯](?:[\s-]?[0-9০-৯]){8}',
);

/// Breadcrumb categories that describe what the app did, never what a person
/// typed or read. Everything else (console, print, ui.click, custom) is dropped.
const Set<String> _keptCategories = <String>{
  'navigation',
  'app.lifecycle',
  'ui.lifecycle',
  'device.orientation',
  'device.event',
  'device.connectivity',
  'http',
};

/// Breadcrumb data that is structural. A URL, a body or route arguments are not.
const Set<String> _keptDataKeys = <String>{
  'state',
  'from',
  'to',
  'method',
  'status_code',
  'reason',
  'action',
  'connectivity',
};

String maskPhonesIn(String text) =>
    text.replaceAllMapped(_phonePattern, (match) => maskPhone(match[0]!));

Object? _scrubValue(Object? value) =>
    value is String ? maskPhonesIn(value) : value;

Breadcrumb? scrubBreadcrumb(Breadcrumb? crumb, [Hint? hint]) {
  if (crumb == null || !_keptCategories.contains(crumb.category)) {
    return null;
  }
  crumb.message = null;
  final data = crumb.data;
  if (data != null) {
    crumb.data = <String, dynamic>{
      for (final entry in data.entries)
        if (_keptDataKeys.contains(entry.key))
          entry.key: _scrubValue(entry.value),
    };
  }
  return crumb;
}

SentryEvent? scrubEvent(SentryEvent event, [Hint? hint]) {
  final message = event.message;
  if (message != null) {
    final template = message.template;
    // Params are dropped: they are exactly where a formatted value would hide.
    event.message = SentryMessage(
      maskPhonesIn(message.formatted),
      template: template == null ? null : maskPhonesIn(template),
    );
  }
  for (final exception in event.exceptions ?? const <SentryException>[]) {
    final value = exception.value;
    if (value != null) {
      exception.value = maskPhonesIn(value);
    }
  }
  final breadcrumbs = event.breadcrumbs;
  if (breadcrumbs != null) {
    event.breadcrumbs = breadcrumbs
        .map(scrubBreadcrumb)
        .whereType<Breadcrumb>()
        .toList();
  }
  final user = event.user;
  if (user != null) {
    event.user = SentryUser(id: user.id);
  }
  event.request = null;
  // Deprecated in the SDK, but integrations can still fill it: scrub it anyway.
  // ignore: deprecated_member_use
  final extra = event.extra;
  if (extra != null) {
    // ignore: deprecated_member_use
    event.extra = <String, dynamic>{
      for (final entry in extra.entries) entry.key: _scrubValue(entry.value),
    };
  }
  return event;
}

void configureSentry(SentryFlutterOptions options, String dsn) {
  options
    ..dsn = dsn
    ..sendDefaultPii = false
    ..attachScreenshot = false
    ..attachViewHierarchy = false
    ..beforeSend = scrubEvent
    ..beforeBreadcrumb = scrubBreadcrumb;
}

/// Runs the app, inside Sentry when a DSN was supplied at build time.
Future<void> runWithCrashReporting(
  FutureOr<void> Function() appRunner, {
  String dsn = Env.sentryDsn,
}) async {
  if (dsn.isEmpty) {
    await appRunner();
    return;
  }
  await SentryFlutter.init(
    (options) => configureSentry(options, dsn),
    appRunner: appRunner,
  );
}
