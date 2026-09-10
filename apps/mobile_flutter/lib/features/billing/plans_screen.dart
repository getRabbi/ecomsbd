import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/billing/billing_providers.dart';
import '../../data/billing/models.dart';
import '../../design/components/badges.dart';
import '../../design/components/surfaces.dart';
import '../../design/glass.dart';
import '../../design/tokens.dart';
import '../shared/data_state.dart';
import '../shared/responsive.dart';

/// Plan, usage and what happens next.
///
/// Master spec sections 26, 27.1 and 27.3, and the shape of this screen comes
/// from all three:
///
/// *   **Nothing here decides anything.** The plan, the usage figures and the
///     purchase button all come from the server. There is no local `isPro`.
/// *   **The call to action is whatever `GET /billing/channel` says it is.**
///     A Play build never renders a bKash link, and the screen does not know
///     why — the policy lives on the server so no two screens can disagree
///     about it (section 27.1).
/// *   **Nothing paid is offered that cannot be bought.** With no provider
///     configured, the plans are shown with what they include and the button
///     says so, rather than opening a checkout that would fail.
class PlansScreen extends ConsumerWidget {
  const PlansScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final overview = ref.watch(billingOverviewProvider);

    return Scaffold(
      backgroundColor: EcomsbdColors.background,
      body: EcomsbdBackground(
        child: SafeArea(
          child: ContentWidthLimit(
            child: RefreshIndicator(
              onRefresh: () async =>
                  ref.refresh(billingOverviewProvider.future),
              child: overview.when(
                loading: () => const _Loading(),
                error: (error, _) => ListView(
                  padding: const EdgeInsets.all(EcomsbdSpacing.page),
                  children: <Widget>[
                    const _Header(),
                    const SizedBox(height: EcomsbdSpacing.lg),
                    ErrorStateCard(
                      error: error is ApiError
                          ? error
                          : ApiError.unexpected(error),
                      onRetry: () => ref.invalidate(billingOverviewProvider),
                    ),
                  ],
                ),
                data: (data) => _Content(data: data),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(EcomsbdSpacing.page),
      children: const <Widget>[
        _Header(),
        SizedBox(height: EcomsbdSpacing.lg),
        Center(child: CircularProgressIndicator()),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header();

  @override
  Widget build(BuildContext context) {
    return Row(
      children: <Widget>[
        IconButton(
          onPressed: () => Navigator.of(context).maybePop(),
          icon: const Icon(Icons.arrow_back_rounded),
          tooltip: 'Back',
        ),
        const Expanded(
          child: Text('Subscription', style: EcomsbdType.pageTitle),
        ),
      ],
    );
  }
}

class _Content extends ConsumerWidget {
  const _Content({required this.data});

  final BillingOverview data;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final subscription = data.subscription;

    return ListView(
      padding: const EdgeInsets.fromLTRB(
        EcomsbdSpacing.page,
        0,
        EcomsbdSpacing.page,
        EcomsbdSpacing.xxl,
      ),
      children: <Widget>[
        const _Header(),
        const SizedBox(height: EcomsbdSpacing.sm),

        // The state that matters most goes first: a failing payment, a plan
        // that is about to end, or credit the seller did not buy.
        if (subscription != null && subscription.isPaymentFailing)
          _PaymentFailedCard(subscription: subscription),
        if (subscription != null &&
            subscription.cancelAtPeriodEnd &&
            !subscription.isPaymentFailing)
          _EndingCard(subscription: subscription),
        if (subscription != null && subscription.isSupportCredit)
          const _SupportCreditNotice(),

        _CurrentPlanCard(data: data),

        const SectionHeader(
          title: 'What you have used',
          subtitle: 'Counted on the server, not on this phone',
        ),
        _UsageCard(usage: data.usage),

        const SectionHeader(title: 'Plans'),
        for (final plan in data.plans)
          _PlanCard(
            plan: plan,
            isCurrent: plan.code == data.entitlements.plan,
            channel: data.channel,
          ),

        const SizedBox(height: EcomsbdSpacing.md),
        _DowngradeNotice(),

        const SizedBox(height: EcomsbdSpacing.md),
        _RestoreRow(),
      ],
    );
  }
}

/// Section 26's rule, said out loud where a seller can read it before they
/// decide. Nothing about a plan ending takes their records away, and a seller
/// weighing whether to keep paying deserves to know that.
class _DowngradeNotice extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.lock_open_rounded, size: 18, color: Tone.info.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text(
                  'Your records stay yours',
                  style: EcomsbdType.sectionTitle,
                ),
                const SizedBox(height: 4),
                Text(
                  'If your plan ends, every order, customer, payout and profit '
                  'figure stays readable. What stops is automation, bulk '
                  'actions and the monthly order allowance — never your history.',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _CurrentPlanCard extends StatelessWidget {
  const _CurrentPlanCard({required this.data});

  final BillingOverview data;

  @override
  Widget build(BuildContext context) {
    final entitlements = data.entitlements;
    final subscription = data.subscription;
    final plan = data.currentPlan;

    final (label, tone) = switch (entitlements.status) {
      'ACTIVE' => ('Active', Tone.good),
      'TRIAL' => ('Trial', Tone.info),
      'GRACE' => ('Payment retrying', Tone.warning),
      'PAST_DUE' => ('Payment failed', Tone.bad),
      'CANCEL_AT_PERIOD_END' => ('Ends soon', Tone.warning),
      'CANCELLED' => ('Cancelled', Tone.neutral),
      'EXPIRED' => ('Ended', Tone.neutral),
      'REFUNDED' => ('Refunded', Tone.neutral),
      'SUSPENDED' => ('Suspended', Tone.bad),
      _ => ('Free plan', Tone.neutral),
    };

    return StrongGlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  plan?.name ?? entitlements.plan,
                  style: EcomsbdType.heroTitle,
                ),
              ),
              StatusChip(label: label, tone: tone),
            ],
          ),
          if (plan != null && !plan.isFree) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              '${plan.price.format()} per month',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (subscription?.statusReason != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            Text(
              subscription!.statusReason!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          if (entitlements.validUntil != null) ...<Widget>[
            const SizedBox(height: 4),
            Text(
              subscription?.cancelAtPeriodEnd ?? false
                  ? 'Access until ${formatDay(entitlements.validUntil!)}'
                  : 'Renews ${formatDay(entitlements.validUntil!)}',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
        ],
      ),
    );
  }
}

/// Section 16 of the Phase F brief, as a screen: a failed payment tells the
/// seller what is happening, when it stops, and that nothing is gone yet.
class _PaymentFailedCard extends StatelessWidget {
  const _PaymentFailedCard({required this.subscription});

  final SubscriptionState subscription;

  @override
  Widget build(BuildContext context) {
    final inGrace = subscription.inGrace && subscription.graceUntil != null;

    return GlassSurface(
      fill: (inGrace ? Tone.warning : Tone.bad).surface,
      borderColor: (inGrace ? Tone.warning : Tone.bad).ink.withValues(
        alpha: 0.2,
      ),
      borderRadius: EcomsbdRadii.cardLarge,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.md),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Icon(
                Icons.credit_card_off_rounded,
                size: 18,
                color: (inGrace ? Tone.warning : Tone.bad).ink,
              ),
              const SizedBox(width: EcomsbdSpacing.sm),
              Expanded(
                child: Text(
                  inGrace
                      ? 'We could not take your payment'
                      : 'Your plan has stopped',
                  style: EcomsbdType.sectionTitle,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            inGrace
                ? 'Everything still works until '
                      '${formatDay(subscription.graceUntil!)} while we try '
                      'again. Check your payment method to keep it running.'
                : 'You are on the Free plan for now. Your orders, customers '
                      'and money records are all still here.',
            style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
          ),
        ],
      ),
    );
  }
}

class _EndingCard extends StatelessWidget {
  const _EndingCard({required this.subscription});

  final SubscriptionState subscription;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: Tone.info.surface,
      borderColor: Tone.info.ink.withValues(alpha: 0.18),
      borderRadius: EcomsbdRadii.cardLarge,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.event_busy_rounded, size: 18, color: Tone.info.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              subscription.currentPeriodEnd == null
                  ? 'This plan will not renew.'
                  : 'This plan will not renew. You keep everything until '
                        '${formatDay(subscription.currentPeriodEnd!)} — you '
                        'have already paid for it.',
              style: EcomsbdType.caption.copyWith(color: Tone.info.ink),
            ),
          ),
        ],
      ),
    );
  }
}

class _SupportCreditNotice extends StatelessWidget {
  const _SupportCreditNotice();

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      fill: Tone.good.surface,
      borderColor: Tone.good.ink.withValues(alpha: 0.18),
      borderRadius: EcomsbdRadii.cardLarge,
      padding: const EdgeInsets.all(EcomsbdSpacing.md),
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.md),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Icon(Icons.card_giftcard_rounded, size: 18, color: Tone.good.ink),
          const SizedBox(width: EcomsbdSpacing.sm),
          Expanded(
            child: Text(
              'This plan was given to you by our support team. It will not '
              'renew or charge you.',
              style: EcomsbdType.caption.copyWith(color: Tone.good.ink),
            ),
          ),
        ],
      ),
    );
  }
}

/// What the shop has spent this period.
///
/// Every figure is the server's. Section 43: the client never counts, because
/// an app that was offline for a day would believe it had quota it had spent.
class _UsageCard extends StatelessWidget {
  const _UsageCard({required this.usage});

  final List<UsageLine> usage;

  @override
  Widget build(BuildContext context) {
    if (usage.isEmpty) {
      return GlassCard(
        child: Text(
          'Nothing metered on this plan.',
          style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
        ),
      );
    }

    return GlassCard(
      child: Column(
        children: <Widget>[
          for (var index = 0; index < usage.length; index++) ...<Widget>[
            if (index > 0) const Divider(height: EcomsbdSpacing.lg),
            _UsageRow(line: usage[index]),
          ],
        ],
      ),
    );
  }
}

class _UsageRow extends StatelessWidget {
  const _UsageRow({required this.line});

  final UsageLine line;

  @override
  Widget build(BuildContext context) {
    final fraction = line.fraction;
    final tone = line.isExhausted
        ? Tone.bad
        : line.isNearLimit
        ? Tone.warning
        : Tone.good;

    return Semantics(
      label: line.unlimited
          ? '${line.label}: ${line.used} used, unlimited'
          : '${line.label}: ${line.used} of ${line.limit} used',
      container: true,
      child: ExcludeSemantics(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: <Widget>[
            Row(
              children: <Widget>[
                Expanded(
                  child: Text(line.label, style: EcomsbdType.bodyStrong),
                ),
                Text(
                  line.unlimited
                      ? '${line.used} · unlimited'
                      : '${line.used} of ${line.limit}',
                  style: EcomsbdType.chip.copyWith(
                    color: line.isExhausted ? tone.ink : EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
            if (fraction != null) ...<Widget>[
              const SizedBox(height: 6),
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(
                  value: fraction,
                  minHeight: 6,
                  backgroundColor: EcomsbdColors.neutralSoft,
                  valueColor: AlwaysStoppedAnimation<Color>(tone.ink),
                ),
              ),
            ],
            if (line.isExhausted) ...<Widget>[
              const SizedBox(height: 6),
              Text(
                'You have used this month’s allowance. Upgrading raises it '
                'straight away, and what you have already recorded stays.',
                style: EcomsbdType.caption.copyWith(color: tone.ink),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _PlanCard extends ConsumerWidget {
  const _PlanCard({
    required this.plan,
    required this.isCurrent,
    required this.channel,
  });

  final Plan plan;
  final bool isCurrent;
  final BillingChannel channel;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GlassCard(
      margin: const EdgeInsets.only(bottom: EcomsbdSpacing.sm),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(child: Text(plan.name, style: EcomsbdType.sectionTitle)),
              if (isCurrent)
                const StatusChip(label: 'Your plan', tone: Tone.good)
              else
                Text(
                  plan.isFree ? 'Free' : plan.price.format(),
                  style: EcomsbdType.bodyStrong,
                ),
            ],
          ),
          const SizedBox(height: EcomsbdSpacing.sm),
          ..._features(plan).map(
            (feature) => Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  const Icon(
                    Icons.check_rounded,
                    size: 14,
                    color: EcomsbdColors.muted2,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      feature,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
          if (!isCurrent && !plan.isFree) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.sm),
            _PurchaseButton(plan: plan, channel: channel),
          ],
        ],
      ),
    );
  }

  /// Section 26's table, in sentences. Rendered from the entitlement values the
  /// server sent, so a configured price or limit change reaches the screen
  /// without a release.
  List<String> _features(Plan plan) {
    final orders = plan.isUnlimited('orders_monthly_limit')
        ? 'Unlimited orders'
        : '${plan.limit('orders_monthly_limit') ?? 0} orders a month';
    final couriers = plan.isUnlimited('courier_account_limit')
        ? 'Unlimited courier accounts'
        : '${plan.limit('courier_account_limit') ?? 0} courier account'
              '${(plan.limit('courier_account_limit') ?? 0) == 1 ? '' : 's'}';

    return <String>[
      orders,
      couriers,
      if (plan.allows('reconciliation'))
        'Full COD reconciliation'
      else
        'View reconciliation results',
      if (plan.isUnlimited('profit_history_days'))
        'Full profit history'
      else
        'Today’s profit only',
      if (plan.allows('bulk_booking')) 'Bulk booking',
      if (plan.allows('advanced_profit')) 'Advanced profit analysis',
      if (plan.allows('csv_export')) 'CSV export of your own data',
      if ((plan.limit('team_member_limit') ?? 1) > 1)
        '${plan.limit('team_member_limit')} team members',
    ];
  }
}

/// The purchase call to action.
///
/// The button's existence, its provider and its wording are all decided by the
/// server's channel policy (section 27.1). A Play build is never offered an
/// external payment link, and this widget does not know that rule — it only
/// renders the answer.
class _PurchaseButton extends ConsumerWidget {
  const _PurchaseButton({required this.plan, required this.channel});

  final Plan plan;
  final BillingChannel channel;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = channel.purchasable.isEmpty
        ? null
        : channel.purchasable.first;

    if (provider == null) {
      // Nothing can be bought in this build. Say so plainly rather than
      // showing a button that opens a checkout which would fail.
      return Row(
        children: <Widget>[
          const Icon(
            Icons.schedule_rounded,
            size: 15,
            color: EcomsbdColors.muted2,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              channel.channel == 'PLAY'
                  ? 'In-app purchase is not switched on yet.'
                  : 'Payments are not switched on yet.',
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
        ],
      );
    }

    return SizedBox(
      width: double.infinity,
      child: FilledButton(
        onPressed: () => _start(context, ref, provider),
        style: FilledButton.styleFrom(
          minimumSize: const Size(0, EcomsbdTouch.minTarget),
          shape: const StadiumBorder(),
        ),
        child: Text('Upgrade with ${provider.displayName}'),
      ),
    );
  }

  Future<void> _start(
    BuildContext context,
    WidgetRef ref,
    BillingProviderState provider,
  ) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      if (provider.provider == 'play') {
        // The Play purchase itself happens through the platform billing
        // library, which is not wired in this build. When it is, the token it
        // returns goes to `verifyPlayPurchase` and the *server* decides what
        // the seller gets.
        messenger.showSnackBar(
          const SnackBar(
            content: Text('Google Play purchase is not switched on yet.'),
          ),
        );
        return;
      }

      final session = await ref
          .read(billingRepositoryProvider)
          .startWebCheckout(plan: plan.code, provider: provider.provider);
      final url = session['redirect_url'] as String?;
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            url == null
                ? 'Checkout could not be started.'
                : 'Continue in your browser to finish paying.',
          ),
        ),
      );
    } on ApiError catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.displayMessage)));
    }
  }
}

/// Master spec section 90's restore path.
///
/// Shown regardless of whether purchasing is available: a seller who bought on
/// another device, or reinstalled, needs it precisely when the purchase flow is
/// not the thing they are trying to do.
class _RestoreRow extends ConsumerStatefulWidget {
  @override
  ConsumerState<_RestoreRow> createState() => _RestoreRowState();
}

class _RestoreRowState extends ConsumerState<_RestoreRow> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Text('Already paid?', style: EcomsbdType.bodyStrong),
                const SizedBox(height: 2),
                Text(
                  'If you bought a plan on another phone, restore it here.',
                  style: EcomsbdType.caption.copyWith(
                    color: EcomsbdColors.muted,
                  ),
                ),
              ],
            ),
          ),
          TextButton(
            onPressed: _busy ? null : _restore,
            style: TextButton.styleFrom(
              minimumSize: const Size(0, EcomsbdTouch.minTarget),
            ),
            child: Text(_busy ? 'Checking…' : 'Restore'),
          ),
        ],
      ),
    );
  }

  Future<void> _restore() async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      // No platform billing library is wired in, so there are no local
      // purchases to send. The call still runs: the server re-reads the
      // subscription, which is what a seller whose renewal notification was
      // missed actually needs.
      final result = await ref
          .read(billingRepositoryProvider)
          .restorePurchases(const <Map<String, String>>[]);
      final restored = result['restored'] as int? ?? 0;
      ref.invalidate(billingOverviewProvider);
      messenger.showSnackBar(
        SnackBar(
          content: Text(
            restored > 0
                ? 'Restored $restored purchase${restored == 1 ? '' : 's'}.'
                : 'No purchases were found on this device.',
          ),
        ),
      );
    } on ApiError catch (error) {
      messenger.showSnackBar(SnackBar(content: Text(error.displayMessage)));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

String formatDay(DateTime value) {
  const months = <String>[
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return '${value.day} ${months[value.month - 1]} ${value.year}';
}
