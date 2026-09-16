import '../../core/money.dart';
import '../../l10n/app_strings.dart';
import '../../l10n/app_locale.dart';

/// Seller-facing labels below are read where no `BuildContext` exists, so
/// they resolve against the active locale directly — the same approach
/// `formatRelative` uses. A language change rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Billing, as the app sees it.
///
/// Master spec section 27.3: **the client never decides what a seller is
/// entitled to.** Everything here is a server answer, rendered. There is no
/// local `isPro`, no cached "paid = true", and no code path that grants
/// anything — the one place the app makes a decision is which button to draw,
/// and even that is answered by `GET /v1/billing/channel`.

/// One plan in the catalogue.
class Plan {
  const Plan({
    required this.code,
    required this.name,
    required this.price,
    required this.entitlements,
  });

  factory Plan.fromJson(Map<String, dynamic> json) {
    return Plan(
      code: json['code'] as String,
      name: json['name'] as String,
      price: Money.fromJson(json['price'] as Map<String, dynamic>),
      entitlements: Map<String, dynamic>.from(
        json['entitlements'] as Map<dynamic, dynamic>? ??
            const <String, dynamic>{},
      ),
    );
  }

  final String code;
  final String name;
  final Money price;
  final Map<String, dynamic> entitlements;

  bool get isFree => price.paisa == 0;

  /// A numeric entitlement, or `null` when it is unlimited or absent.
  ///
  /// `-1` is the server's sentinel for "no cap"; returning `null` for it means
  /// a screen that forgets to handle unlimited renders nothing rather than the
  /// literal `-1`.
  int? limit(String key) {
    final value = entitlements[key];
    if (value is! int || value == -1) return null;
    return value;
  }

  bool allows(String key) {
    final value = entitlements[key];
    if (value is bool) return value;
    if (value is int) return value != 0;
    return false;
  }

  bool isUnlimited(String key) => entitlements[key] == -1;
}

/// What the shop's plan currently grants.
class Entitlements {
  const Entitlements({
    required this.plan,
    required this.status,
    required this.entitlements,
    this.source,
    this.validUntil,
  });

  factory Entitlements.fromJson(Map<String, dynamic> json) {
    return Entitlements(
      plan: json['plan'] as String,
      status: json['status'] as String,
      source: json['source'] as String?,
      validUntil: json['valid_until'] == null
          ? null
          : DateTime.parse(json['valid_until'] as String).toLocal(),
      entitlements: Map<String, dynamic>.from(
        json['entitlements'] as Map<dynamic, dynamic>? ??
            const <String, dynamic>{},
      ),
    );
  }

  final String plan;
  final String status;
  final String? source;
  final DateTime? validUntil;
  final Map<String, dynamic> entitlements;

  bool get isFree => plan == 'free';
}

/// The shop's subscription record.
class SubscriptionState {
  const SubscriptionState({
    required this.id,
    required this.plan,
    required this.status,
    required this.provider,
    required this.distributionChannel,
    required this.cancelAtPeriodEnd,
    required this.inGrace,
    this.currentPeriodEnd,
    this.trialEnd,
    this.graceUntil,
    this.statusReason,
    this.verifiedAt,
  });

  factory SubscriptionState.fromJson(Map<String, dynamic> json) {
    DateTime? parse(String key) => json[key] == null
        ? null
        : DateTime.parse(json[key] as String).toLocal();

    return SubscriptionState(
      id: json['id'] as String,
      plan: json['plan'] as String,
      status: json['status'] as String,
      provider: json['provider'] as String,
      distributionChannel: json['distribution_channel'] as String,
      cancelAtPeriodEnd: json['cancel_at_period_end'] as bool? ?? false,
      inGrace: json['in_grace'] as bool? ?? false,
      currentPeriodEnd: parse('current_period_end'),
      trialEnd: parse('trial_end'),
      graceUntil: parse('grace_until'),
      statusReason: json['status_reason'] as String?,
      verifiedAt: parse('verified_at'),
    );
  }

  final String id;
  final String plan;
  final String status;
  final String provider;
  final String distributionChannel;
  final bool cancelAtPeriodEnd;
  final bool inGrace;
  final DateTime? currentPeriodEnd;
  final DateTime? trialEnd;
  final DateTime? graceUntil;
  final String? statusReason;
  final DateTime? verifiedAt;

  bool get isPaymentFailing => status == 'GRACE' || status == 'PAST_DUE';
  bool get hasEnded =>
      status == 'EXPIRED' || status == 'CANCELLED' || status == 'REFUNDED';

  /// Support-issued credit. Worth naming on screen, because it explains why a
  /// plan is active that the seller does not remember paying for.
  bool get isSupportCredit => provider == 'manual_admin';
}

/// One metered entitlement's spend this period.
class UsageLine {
  const UsageLine({
    required this.entitlement,
    required this.period,
    required this.periodKey,
    required this.used,
    required this.limit,
    required this.unlimited,
    this.remaining,
  });

  factory UsageLine.fromJson(Map<String, dynamic> json) {
    return UsageLine(
      entitlement: json['entitlement'] as String,
      period: json['period'] as String,
      periodKey: json['period_key'] as String,
      used: json['used'] as int? ?? 0,
      limit: json['limit'] as int? ?? -1,
      unlimited: json['unlimited'] as bool? ?? false,
      remaining: json['remaining'] as int?,
    );
  }

  final String entitlement;
  final String period;
  final String periodKey;
  final int used;
  final int limit;
  final bool unlimited;
  final int? remaining;

  /// `0.0`–`1.0`, or `null` when there is no cap to be a fraction of.
  double? get fraction {
    if (unlimited || limit <= 0) return null;
    return (used / limit).clamp(0.0, 1.0);
  }

  bool get isExhausted => !unlimited && limit > 0 && used >= limit;

  /// Approaching the cap. The threshold is here rather than in a widget so
  /// every surface warns at the same point.
  bool get isNearLimit {
    final value = fraction;
    return value != null && value >= 0.8 && !isExhausted;
  }

  /// Seller-facing label. The server sends stable keys; the words live here.
  String get label => switch (entitlement) {
    'orders_monthly_limit' => 'Orders this month',
    'risk_checks_daily' => 'Risk checks today',
    'sms_segments_monthly' => 'SMS this month',
    'ai_parse_monthly' => 'AI paste-parse this month',
    _ => entitlement,
  };
}

/// One billing provider's state in this build.
class BillingProviderState {
  const BillingProviderState({
    required this.provider,
    required this.available,
    required this.blocker,
    required this.allowedInChannel,
  });

  factory BillingProviderState.fromJson(Map<String, dynamic> json) {
    return BillingProviderState(
      provider: json['provider'] as String,
      available: json['available'] as bool? ?? false,
      blocker: json['blocker'] as String? ?? 'NONE',
      allowedInChannel: json['allowed_in_channel'] as bool? ?? false,
    );
  }

  final String provider;
  final bool available;
  final String blocker;
  final bool allowedInChannel;

  bool get isPurchasable => available && allowedInChannel;

  String get displayName => switch (provider) {
    'play' => 'Google Play',
    'bkash_web' => 'bKash',
    'manual_admin' => 'Support credit',
    _ => provider,
  };
}

/// What this build may offer for purchase (master spec section 27.1).
///
/// The whole reason this type exists: the *server* decides whether a purchase
/// CTA may be shown, so no screen has to remember a store policy and no two
/// screens can disagree about it.
class BillingChannel {
  const BillingChannel({
    required this.channel,
    required this.canPurchase,
    required this.allowsExternalPaymentLink,
    required this.providers,
    this.unavailableMessageBn,
  });

  factory BillingChannel.fromJson(Map<String, dynamic> json) {
    return BillingChannel(
      channel: json['channel'] as String,
      canPurchase: json['can_purchase'] as bool? ?? false,
      allowsExternalPaymentLink:
          json['allows_external_payment_link'] as bool? ?? false,
      unavailableMessageBn: json['unavailable_message_bn'] as String?,
      providers: <BillingProviderState>[
        for (final row
            in (json['providers'] as List<dynamic>? ?? const <dynamic>[]))
          BillingProviderState.fromJson(row as Map<String, dynamic>),
      ],
    );
  }

  final String channel;
  final bool canPurchase;
  final bool allowsExternalPaymentLink;
  final List<BillingProviderState> providers;
  final String? unavailableMessageBn;

  Iterable<BillingProviderState> get purchasable =>
      providers.where((p) => p.isPurchasable);
}

/// One line of billing history, including refusals.
class BillingEvent {
  const BillingEvent({
    required this.id,
    required this.provider,
    required this.plan,
    required this.kind,
    required this.state,
    required this.amount,
    required this.verificationResult,
    required this.occurredAt,
    this.detail,
  });

  factory BillingEvent.fromJson(Map<String, dynamic> json) {
    return BillingEvent(
      id: json['id'] as String,
      provider: json['provider'] as String,
      plan: json['plan'] as String,
      kind: json['kind'] as String,
      state: json['state'] as String,
      amount: Money.fromJson(json['amount'] as Map<String, dynamic>),
      verificationResult: json['verification_result'] as String,
      detail: json['detail'] as String?,
      occurredAt: DateTime.parse(json['occurred_at'] as String).toLocal(),
    );
  }

  final String id;
  final String provider;
  final String plan;
  final String kind;
  final String state;
  final Money amount;
  final String verificationResult;
  final String? detail;
  final DateTime occurredAt;

  bool get succeeded => state == 'VERIFIED';

  /// Why a refusal happened, in the seller's terms. The machine codes are
  /// stable; these sentences are the app's job.
  String get outcomeLabel => switch (verificationResult) {
    'VERIFIED' => _t('vo.confirmed'),
    'REJECTED' => _t('vo.rejected'),
    'UNAVAILABLE' => _t('vo.unavailable'),
    'NOT_CONFIGURED' => _t('vo.notConfigured'),
    'WRONG_TENANT' => _t('vo.wrongTenant'),
    'UNKNOWN_PRODUCT' => _t('vo.unknownProduct'),
    'WRONG_PACKAGE' => _t('vo.wrongPackage'),
    _ => verificationResult,
  };
}

/// Everything the plans screen needs, fetched together.
class BillingOverview {
  const BillingOverview({
    required this.plans,
    required this.entitlements,
    required this.usage,
    required this.channel,
    this.subscription,
  });

  final List<Plan> plans;
  final Entitlements entitlements;
  final List<UsageLine> usage;
  final BillingChannel channel;
  final SubscriptionState? subscription;

  Plan? get currentPlan {
    for (final plan in plans) {
      if (plan.code == entitlements.plan) return plan;
    }
    return null;
  }
}
