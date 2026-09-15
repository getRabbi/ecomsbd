import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/env.dart';
import '../design/theme.dart';
import 'router.dart';

/// The application root.
class EcomsbdApp extends ConsumerWidget {
  const EcomsbdApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return MaterialApp.router(
      // The locked product name. Shown in the Android task switcher.
      title: Env.appName,
      debugShowCheckedModeBanner: false,
      theme: buildEcomsbdTheme(),
      // Master spec section 57 lists a dark-mode project as a V1 non-goal, so
      // the single light theme is used regardless of the system setting.
      themeMode: ThemeMode.light,
      routerConfig: ref.watch(routerProvider),
      builder: (context, child) {
        // Respect the system text scale so the app stays legible for sellers
        // who have enlarged type (master spec section 124), while capping it
        // where the money hero would otherwise overflow its card.
        final scale = MediaQuery.textScalerOf(
          context,
        ).clamp(minScaleFactor: 0.85, maxScaleFactor: 1.35);
        return MediaQuery(
          data: MediaQuery.of(context).copyWith(textScaler: scale),
          // Custom glass routes can sit outside a Scaffold/Material. Give
          // them the theme's text style instead of MaterialApp's yellow
          // double-underline fallback. Explicit text decorations still win.
          child: DefaultTextStyle(
            style: Theme.of(context).textTheme.bodyMedium!,
            // Fallback status bar style. Pushed screens that set none kept
            // whatever was last set, which could leave white icons on a light
            // page. Screens that set their own (EcomsbdScaffold) still win.
            child: AnnotatedRegion<SystemUiOverlayStyle>(
              value: ecomsbdLightOverlay,
              child: child ?? const SizedBox.shrink(),
            ),
          ),
        );
      },
    );
  }
}
