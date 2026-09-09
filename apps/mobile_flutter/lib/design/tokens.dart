import 'package:flutter/material.dart';

/// Design tokens for the ecomsbd premium Reddit-glass UI.
///
/// Values are lifted directly from the locked HTML prototype's `:root` block so
/// the Flutter app and the reference render the same colours and shadows. The
/// only place this file deliberately departs from the prototype is the type
/// scale — see [EcomsbdType].
///
/// Nothing in the app hard-codes a colour, radius or shadow; components take
/// them from here so a palette change is one edit rather than an audit.
class EcomsbdColors {
  const EcomsbdColors._();

  // --- surfaces -------------------------------------------------------------
  /// `--bg`
  static const Color background = Color(0xFFEEF2F6);

  /// `--bg2`
  static const Color backgroundLight = Color(0xFFF8FAFC);

  /// Warm/cool wash behind the page, matching the prototype's body gradient.
  static const Color washOrange = Color(0x18FF4500);
  static const Color washBlue = Color(0x1A2C64D8);
  static const Color gradientTop = Color(0xFFFBFCFD);
  static const Color gradientMid = Color(0xFFEDF1F5);
  static const Color gradientBottom = Color(0xFFE9EEF3);

  // --- ink ------------------------------------------------------------------
  /// `--ink`
  static const Color ink = Color(0xFF11161D);

  /// `--muted`
  static const Color muted = Color(0xFF697481);

  /// `--muted2`
  static const Color muted2 = Color(0xFF99A3AD);

  // --- glass ----------------------------------------------------------------
  /// `--glass`
  static const Color glass = Color(0xABFFFFFF);

  /// `--glass-strong`
  static const Color glassStrong = Color(0xE0FFFFFF);

  /// `--glass-dark`
  static const Color glassDark = Color(0x8A0E1827);

  /// `--stroke`
  static const Color stroke = Color(0x13151F2B);

  /// `--whiteStroke`
  static const Color whiteStroke = Color(0xF5FFFFFF);

  // --- brand ----------------------------------------------------------------
  /// `--orange` — the Reddit-orange accent.
  static const Color orange = Color(0xFFFF4500);

  /// `--orange2`
  static const Color orangeLight = Color(0xFFFF7D52);

  /// `--orangeSoft`
  static const Color orangeSoft = Color(0x1CFF4500);

  // --- semantic -------------------------------------------------------------
  // Master spec section 122: state is never communicated by colour alone; every
  // component pairing these with a status also renders an icon or a label.
  static const Color green = Color(0xFF11814A);
  static const Color greenSoft = Color(0x1C11814A);
  static const Color red = Color(0xFFC73A40);
  static const Color redSoft = Color(0x1CC73A40);
  static const Color amber = Color(0xFF9D6400);
  static const Color amberSoft = Color(0x2EFFB835);
  static const Color blue = Color(0xFF2C64D8);
  static const Color blueSoft = Color(0x1C2C64D8);
  static const Color neutralSoft = Color(0x1A5A6672);
  static const Color neutralInk = Color(0xFF59636C);

  // --- hero navy ------------------------------------------------------------
  /// `--navy`, `--navy2`, `--navy3`
  static const Color navy = Color(0xFF07111E);
  static const Color navyMid = Color(0xFF0C2744);
  static const Color navyLight = Color(0xFF285781);
  static const Color navyHeroTop = Color(0xFF2C5E8D);
  static const Color navyHeroUpper = Color(0xFF12385F);
  static const Color navyHeroLower = Color(0xFF091D34);
  static const Color navyHeroBottom = Color(0xFF06111F);

  /// Highlight blooms inside the navy hero.
  static const Color heroGlowBlue = Color(0x7068ABFF);
  static const Color heroGlowOrange = Color(0x33FF4500);

  // --- chart / bar tracks ---------------------------------------------------
  static const Color trackLight = Color(0xFFE7ECF0);
  static const Color trackMuted = Color(0xFFE5EBEF);
  static const Color gridline = Color(0xFFDFE5EA);
  static const Color chartLabel = Color(0xFF8D98A2);
  static const Color donutRemainder = Color(0xFFDCE2E7);
  static const Color barComparisonBase = Color(0xFFD7E1EA);
  static const Color rowIconBackground = Color(0xFFEAF0F4);
  static const Color rowIconInk = Color(0xFF53616E);
  static const Color miniTile = Color(0xFFF3F6F8);
  static const Color tagBackground = Color(0xFFEDF1F4);
  static const Color tagInk = Color(0xFF596570);
}

/// Corner radii. `--r-xl` … `--r-sm`.
class EcomsbdRadii {
  const EcomsbdRadii._();

  static const double xl = 30;
  static const double lg = 24;
  static const double md = 18;
  static const double sm = 13;
  static const double pill = 999;

  static const BorderRadius cardLarge = BorderRadius.all(Radius.circular(lg));
  static const BorderRadius cardMedium = BorderRadius.all(Radius.circular(md));
  static const BorderRadius cardSmall = BorderRadius.all(Radius.circular(sm));
  static const BorderRadius round = BorderRadius.all(Radius.circular(pill));
}

/// Spacing scale. The prototype uses a tight 8–14px rhythm; these are the
/// values that actually recur in it.
class EcomsbdSpacing {
  const EcomsbdSpacing._();

  static const double xxs = 4;
  static const double xs = 6;
  static const double sm = 9;
  static const double md = 13;
  static const double lg = 17;
  static const double xl = 22;
  static const double xxl = 30;

  /// Horizontal page padding, matching `.page-body`.
  static const double page = 13;

  /// Clearance for the floating bottom navigation so content is never covered.
  static const double bottomNavClearance = 112;
}

/// Elevation. `--shadow` and `--shadow-soft`.
class EcomsbdShadows {
  const EcomsbdShadows._();

  static const List<BoxShadow> strong = <BoxShadow>[
    BoxShadow(color: Color(0x1C0F1823), blurRadius: 54, offset: Offset(0, 18)),
    BoxShadow(color: Color(0x0A0F1823), blurRadius: 12, offset: Offset(0, 3)),
  ];

  static const List<BoxShadow> soft = <BoxShadow>[
    BoxShadow(color: Color(0x120F1823), blurRadius: 28, offset: Offset(0, 8)),
  ];

  static const List<BoxShadow> pill = <BoxShadow>[
    BoxShadow(color: Color(0x1A141D28), blurRadius: 30, offset: Offset(0, 10)),
  ];

  static const List<BoxShadow> bottomNav = <BoxShadow>[
    BoxShadow(color: Color(0x2B121A24), blurRadius: 52, offset: Offset(0, 18)),
  ];

  static const List<BoxShadow> accent = <BoxShadow>[
    BoxShadow(color: Color(0x33FF4500), blurRadius: 18, offset: Offset(0, 8)),
  ];
}

/// Typography.
///
/// This is the one place the implementation deliberately departs from the
/// prototype. The HTML is a desktop mock whose body copy sits at 8–10 CSS px;
/// rendered on a real 360dp Android screen those sizes are below the minimum
/// legible size and would fail master spec sections 52 and 124 ("large tap
/// targets", "scalable text"). The *hierarchy* is preserved exactly — hero
/// money dominates, section titles read as titles, supporting copy recedes —
/// while every size is lifted to a readable floor.
///
/// Recorded as an ADR: docs/ADR/0003-type-scale.md.
class EcomsbdType {
  const EcomsbdType._();

  static const String family = 'RedditSans';

  /// Reddit Sans has no Bengali coverage; Bangla copy falls through to Noto.
  static const List<String> fallback = <String>['NotoSansBengali'];

  static const TextStyle heroMoney = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 36,
    height: 1.0,
    fontWeight: FontWeight.w800,
    letterSpacing: -1.5,
    fontFeatures: <FontFeature>[FontFeature.tabularFigures()],
  );

  static const TextStyle heroTitle = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 28,
    height: 1.1,
    fontWeight: FontWeight.w800,
    letterSpacing: -0.9,
  );

  static const TextStyle pageTitle = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 25,
    height: 1.15,
    fontWeight: FontWeight.w800,
    letterSpacing: -0.7,
  );

  static const TextStyle sectionTitle = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 16,
    height: 1.25,
    fontWeight: FontWeight.w700,
    letterSpacing: -0.2,
  );

  static const TextStyle metricValue = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 19,
    height: 1.15,
    fontWeight: FontWeight.w800,
    fontFeatures: <FontFeature>[FontFeature.tabularFigures()],
  );

  static const TextStyle bodyStrong = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 14,
    height: 1.3,
    fontWeight: FontWeight.w700,
  );

  static const TextStyle body = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 13,
    height: 1.45,
    fontWeight: FontWeight.w400,
  );

  static const TextStyle label = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 12,
    height: 1.35,
    fontWeight: FontWeight.w600,
  );

  static const TextStyle caption = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 11.5,
    height: 1.4,
    fontWeight: FontWeight.w400,
  );

  /// Uppercase micro-labels (`.eyebrow`, `.kpi small`). Used sparingly and
  /// always paired with a larger value beneath, so the small size carries no
  /// information on its own.
  static const TextStyle eyebrow = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 10,
    height: 1.2,
    fontWeight: FontWeight.w800,
    letterSpacing: 1.1,
  );

  /// Chips and badges.
  static const TextStyle chip = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 11.5,
    height: 1.1,
    fontWeight: FontWeight.w700,
  );

  /// Money always renders with tabular figures so columns of amounts align.
  static const TextStyle money = TextStyle(
    fontFamily: family,
    fontFamilyFallback: fallback,
    fontSize: 14,
    height: 1.2,
    fontWeight: FontWeight.w700,
    fontFeatures: <FontFeature>[FontFeature.tabularFigures()],
  );
}

/// Minimum interactive sizes (master spec section 124).
class EcomsbdTouch {
  const EcomsbdTouch._();

  /// Android's minimum recommended touch target.
  static const double minTarget = 48;

  /// Height of the floating bottom navigation bar.
  static const double bottomNavHeight = 74;
}
