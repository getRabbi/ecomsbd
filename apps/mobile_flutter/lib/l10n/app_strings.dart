import 'package:flutter/foundation.dart' show SynchronousFuture;
import 'package:flutter/widgets.dart';

import 'app_locale.dart';
import 'app_strings_data.dart';

/// Seller-facing copy in the selected language.
///
/// One catalogue, two languages, shared keys — deliberately not two screen
/// trees. A key that exists in only one language falls back to English rather
/// than rendering blank, and in debug an unknown key trips an assert so a typo
/// is found while building the screen rather than by a seller.
///
/// What is *not* in here, on purpose: API values, enum and database keys, log
/// lines, developer text, courier and provider brand names, and technical
/// identifiers. Those are contract, not copy, and translating them would break
/// the thing they identify.
@immutable
class AppStrings {
  const AppStrings(this.locale);

  final AppLocale locale;

  static const LocalizationsDelegate<AppStrings> delegate =
      _AppStringsDelegate();

  /// The catalogue for the language in force at this point in the tree.
  ///
  /// Falls back to Bangla rather than throwing if used above the delegate,
  /// which only happens in a widget test that forgot it.
  static AppStrings of(BuildContext context) =>
      Localizations.of<AppStrings>(context, AppStrings) ??
      const AppStrings(AppLocale.bn);

  /// Look up [key], substituting `{name}` placeholders from [vars].
  String t(String key, [Map<String, Object?>? vars]) {
    final table = locale == AppLocale.en ? englishStrings : banglaStrings;
    var value = table[key] ?? englishStrings[key];
    assert(value != null, 'Missing translation for "$key"');
    value ??= key;
    if (vars != null && vars.isNotEmpty) {
      for (final entry in vars.entries) {
        value = value!.replaceAll('{${entry.key}}', '${entry.value ?? ''}');
      }
    }
    return value!;
  }

  /// Count-aware lookup: `<key>.one` when [count] is 1, else `<key>.other`.
  ///
  /// Bangla does not inflect these the way English does, so both forms usually
  /// carry the same wording there; the split exists so the English side reads
  /// correctly rather than as "1 orders".
  String plural(String key, int count, [Map<String, Object?>? vars]) => t(
    '$key.${count == 1 ? 'one' : 'other'}',
    <String, Object?>{'count': count, ...?vars},
  );
}

class _AppStringsDelegate extends LocalizationsDelegate<AppStrings> {
  const _AppStringsDelegate();

  @override
  bool isSupported(Locale locale) =>
      locale.languageCode == 'bn' || locale.languageCode == 'en';

  @override
  Future<AppStrings> load(Locale locale) => SynchronousFuture<AppStrings>(
    AppStrings(AppLocale.fromCode(locale.languageCode)),
  );

  @override
  bool shouldReload(_AppStringsDelegate old) => false;
}

/// `context.tr('orders.title')` at every call site.
extension AppStringsContext on BuildContext {
  AppStrings get strings => AppStrings.of(this);

  String tr(String key, [Map<String, Object?>? vars]) =>
      AppStrings.of(this).t(key, vars);

  String trPlural(String key, int count, [Map<String, Object?>? vars]) =>
      AppStrings.of(this).plural(key, count, vars);
}
