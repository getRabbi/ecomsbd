import 'dart:async';

import 'package:ecomsbd/l10n/app_locale.dart';

/// Suite-wide setup, run once before any test in `test/`.
///
/// The app reads its language from two places that must always agree:
/// `Localizations` (what `context.tr` uses) and the `activeAppLocale` global
/// (what the `_t` helpers use where there is no BuildContext).
/// `LocaleController` sets both, so they are never out of step in the app.
///
/// Widget tests get `Localizations` from `wrapForTest`, but a model or
/// repository test never pumps a widget and so never passes through it. Pinning
/// the global here makes every test in the suite — widget or not — read the
/// same language, which is what stops a data-layer assertion passing or failing
/// depending on which tests ran before it.
Future<void> testExecutable(FutureOr<void> Function() testMain) async {
  activeAppLocale = AppLocale.en;
  await testMain();
}
