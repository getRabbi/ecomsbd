import 'dart:ui' show Locale, PlatformDispatcher;

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// The two languages the product ships in.
///
/// Master spec section 52: Bangla is the primary seller-facing language, and
/// English is the alternative a seller may prefer. There is no third state and
/// no "system" option that could leave half the app in one language and half
/// in the other — the seller picks one, and it applies everywhere, including
/// onboarding and the auth screens before a session exists.
enum AppLocale {
  bn,
  en;

  /// The name of the language *in* that language, which is how a language
  /// picker has to read: someone who cannot read English still has to find
  /// "বাংলা", and vice versa.
  String get nativeLabel => switch (this) {
    AppLocale.bn => 'বাংলা',
    AppLocale.en => 'English',
  };

  Locale get locale => Locale(name);

  static AppLocale fromCode(String? code) {
    if (code == null) return _deviceDefault;
    return switch (code.toLowerCase().split(RegExp('[_-]')).first) {
      'bn' => AppLocale.bn,
      'en' => AppLocale.en,
      _ => _deviceDefault,
    };
  }

  /// What to use before the seller has chosen.
  ///
  /// The device's own language, when the app has it. A Bangladeshi seller
  /// whose phone is in Bangla should never have to find the language setting
  /// to read the first screen.
  static AppLocale get _deviceDefault {
    for (final locale in PlatformDispatcher.instance.locales) {
      if (locale.languageCode == 'bn') return AppLocale.bn;
      if (locale.languageCode == 'en') return AppLocale.en;
    }
    return AppLocale.bn;
  }
}

/// The selected language, readable without a `BuildContext`.
///
/// Almost everything reads the language through `AppStrings.of(context)`, which
/// rebuilds correctly when it changes. This mirror exists for the few places
/// that format a seller-facing string with no widget in reach — chiefly
/// `ApiError.displayMessage`, which picks between the server's Bangla and
/// English copy. Kept in sync by [LocaleController]; never written elsewhere.
AppLocale activeAppLocale = AppLocale.bn;

const String _localeKey = 'ecomsbd.locale.v1';

/// Reads the stored language before the first frame.
///
/// Called from `main()` so the app's first paint is already in the seller's
/// language rather than flipping a frame later.
Future<AppLocale> loadSavedLocale() async {
  try {
    final prefs = await SharedPreferences.getInstance();
    final saved = AppLocale.fromCode(prefs.getString(_localeKey));
    activeAppLocale = saved;
    return saved;
  } on Object {
    // A preferences failure must not stop launch; the device default is a
    // perfectly good answer.
    return activeAppLocale;
  }
}

/// Owns the selected language and persists it.
class LocaleController extends StateNotifier<AppLocale> {
  LocaleController(super.initial) {
    activeAppLocale = state;
  }

  Future<void> select(AppLocale locale) async {
    if (locale == state) return;
    // The UI switches on this frame; writing to disk is not awaited by the
    // rebuild, so changing language never waits on storage.
    state = locale;
    activeAppLocale = locale;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_localeKey, locale.name);
    } on Object {
      // The choice still applies for this run even if it could not be stored.
    }
  }
}

/// Overridden in `main()` with the value read from storage.
final localeProvider = StateNotifierProvider<LocaleController, AppLocale>(
  (ref) => LocaleController(activeAppLocale),
);
