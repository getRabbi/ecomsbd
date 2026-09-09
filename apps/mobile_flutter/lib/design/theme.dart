import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import 'tokens.dart';

/// The app theme.
///
/// Material is used as the widget substrate only. The visual language is the
/// locked Reddit-glass design, so this theme mostly *removes* Material's
/// defaults — surface tints, ripple-heavy buttons, its own type ramp — and
/// substitutes the prototype's tokens. Components take their styling from
/// [EcomsbdColors] and friends rather than from `Theme.of(context)`, which is
/// why this file is short.
///
/// Master spec section 57 lists a dark-mode project as a V1 non-goal, so a
/// single light theme is defined and the system dark setting is not followed.
ThemeData buildEcomsbdTheme() {
  const colorScheme = ColorScheme(
    brightness: Brightness.light,
    primary: EcomsbdColors.orange,
    onPrimary: Colors.white,
    secondary: EcomsbdColors.blue,
    onSecondary: Colors.white,
    error: EcomsbdColors.red,
    onError: Colors.white,
    surface: EcomsbdColors.background,
    onSurface: EcomsbdColors.ink,
  );

  return ThemeData(
    useMaterial3: true,
    colorScheme: colorScheme,
    scaffoldBackgroundColor: EcomsbdColors.background,
    fontFamily: EcomsbdType.family,
    fontFamilyFallback: EcomsbdType.fallback,

    // Material 3 tints surfaces by elevation; the glass system controls its own
    // surface colours and the tint muddies them.
    applyElevationOverlayColor: false,
    splashFactory: InkSparkle.splashFactory,

    textTheme: const TextTheme(
      displayLarge: EcomsbdType.heroMoney,
      headlineLarge: EcomsbdType.heroTitle,
      headlineMedium: EcomsbdType.pageTitle,
      titleMedium: EcomsbdType.sectionTitle,
      titleSmall: EcomsbdType.bodyStrong,
      bodyLarge: EcomsbdType.body,
      bodyMedium: EcomsbdType.body,
      bodySmall: EcomsbdType.caption,
      labelLarge: EcomsbdType.label,
      labelMedium: EcomsbdType.chip,
      labelSmall: EcomsbdType.eyebrow,
    ).apply(bodyColor: EcomsbdColors.ink, displayColor: EcomsbdColors.ink),

    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0xCFFFFFFF),
      // Comfortably above the 48dp minimum touch target.
      contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
      hintStyle: EcomsbdType.body.copyWith(color: EcomsbdColors.muted2),
      labelStyle: EcomsbdType.label.copyWith(color: EcomsbdColors.muted),
      border: const OutlineInputBorder(
        borderRadius: EcomsbdRadii.cardSmall,
        borderSide: BorderSide(color: EcomsbdColors.stroke),
      ),
      enabledBorder: const OutlineInputBorder(
        borderRadius: EcomsbdRadii.cardSmall,
        borderSide: BorderSide(color: EcomsbdColors.stroke),
      ),
      focusedBorder: const OutlineInputBorder(
        borderRadius: EcomsbdRadii.cardSmall,
        borderSide: BorderSide(color: EcomsbdColors.orange, width: 1.6),
      ),
      errorBorder: const OutlineInputBorder(
        borderRadius: EcomsbdRadii.cardSmall,
        borderSide: BorderSide(color: EcomsbdColors.red, width: 1.4),
      ),
      focusedErrorBorder: const OutlineInputBorder(
        borderRadius: EcomsbdRadii.cardSmall,
        borderSide: BorderSide(color: EcomsbdColors.red, width: 1.6),
      ),
    ),

    dividerTheme: const DividerThemeData(
      color: EcomsbdColors.stroke,
      thickness: 1,
      space: 1,
    ),

    snackBarTheme: SnackBarThemeData(
      backgroundColor: const Color(0xFF111820),
      contentTextStyle: EcomsbdType.label.copyWith(color: Colors.white),
      behavior: SnackBarBehavior.floating,
      shape: const RoundedRectangleBorder(borderRadius: EcomsbdRadii.cardSmall),
    ),

    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: EcomsbdColors.orange,
      linearTrackColor: EcomsbdColors.trackLight,
    ),
  );
}

/// Status-bar styling for the light page background.
const SystemUiOverlayStyle ecomsbdLightOverlay = SystemUiOverlayStyle(
  statusBarColor: Colors.transparent,
  statusBarIconBrightness: Brightness.dark,
  statusBarBrightness: Brightness.light,
  systemNavigationBarColor: EcomsbdColors.background,
  systemNavigationBarIconBrightness: Brightness.dark,
);

/// Status-bar styling for screens whose top area is the navy hero.
const SystemUiOverlayStyle ecomsbdDarkOverlay = SystemUiOverlayStyle(
  statusBarColor: Colors.transparent,
  statusBarIconBrightness: Brightness.light,
  statusBarBrightness: Brightness.dark,
  systemNavigationBarColor: EcomsbdColors.background,
  systemNavigationBarIconBrightness: Brightness.dark,
);
