import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../data/commerce/commerce_providers.dart';
import '../../data/commerce/risk_repository.dart';
import 'external_risk_card.dart';
import '../../design/components/badges.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';
import '../insights/rto_screen.dart';
import '../shared/inputs.dart';

/// Risk check.
///
/// What this screen is, precisely: the seller's *own* delivery history with a
/// number, and a band computed from it by a rule they can check. Three things
/// it therefore refuses to do:
///
/// * **Claim to know a customer it has no history with.** An unknown number
///   returns "not enough history", not a reassuring "low risk" — and that
///   distinction is the whole point of the screen (master spec section 121).
/// * **Speak about the person.** Section 130 forbids "fraud" or "scammer"; the
///   band is about the *order*, and the copy stays there.
/// * **Imply a network.** Every result carries the caveat that this is one
///   shop's own history. A seller must never read it as a shared reputation,
///   because no such thing is computed and none could be without a licensed
///   source.
class RiskCheckScreen extends ConsumerStatefulWidget {
  const RiskCheckScreen({super.key});

  @override
  ConsumerState<RiskCheckScreen> createState() => _RiskCheckScreenState();
}

class _RiskCheckScreenState extends ConsumerState<RiskCheckScreen> {
  final TextEditingController _phone = TextEditingController();
  bool _busy = false;
  RiskCheck? _result;
  ApiError? _error;

  @override
  void dispose() {
    _phone.dispose();
    super.dispose();
  }

  Future<void> _check() async {
    final phone = _phone.text.trim();
    if (phone.isEmpty) {
      return;
    }
    FocusScope.of(context).unfocus();
    setState(() {
      _busy = true;
      _error = null;
      _result = null;
    });
    try {
      final result = await ref.read(riskRepositoryProvider).check(phone);
      if (mounted) {
        setState(() => _result = result);
      }
    } on ApiError catch (error) {
      // Quota exhaustion and a plan that excludes the feature both land here,
      // and both are things the seller can act on — so they are shown in place
      // rather than thrown away in a snackbar.
      if (mounted) {
        setState(() => _error = error);
      }
    } finally {
      if (mounted) {
        setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final result = _result;
    final error = _error;

    return DetailScaffold(
      title: context.tr('rc.title'),
      subtitle: context.tr('rc.subtitle'),
      children: <Widget>[
        const ExternalRiskCard(),
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              LabelledField(
                label: context.tr('rc.phoneLabel'),
                controller: _phone,
                hint: context.tr('rc.phoneHint'),
                keyboardType: TextInputType.phone,
                inputFormatters: <TextInputFormatter>[
                  LengthLimitingTextInputFormatter(24),
                ],
                onChanged: (_) => setState(() {}),
              ),
              const SizedBox(height: EcomsbdSpacing.md),
              FilledButton(
                onPressed: _busy || _phone.text.trim().isEmpty ? null : _check,
                style: FilledButton.styleFrom(
                  minimumSize: const Size.fromHeight(EcomsbdTouch.minTarget),
                  backgroundColor: EcomsbdColors.navy,
                  foregroundColor: Colors.white,
                  shape: const StadiumBorder(),
                  textStyle: EcomsbdType.label,
                ),
                child: Text(
                  _busy ? context.tr('rc.checking') : context.tr('rc.check'),
                ),
              ),
            ],
          ),
        ),
        if (error != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.md),
          ErrorStateCard(error: error, onRetry: _check),
        ],
        if (result != null) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.md),
          _VerdictCard(result: result),
          const SizedBox(height: EcomsbdSpacing.md),
          _HistoryCard(result: result),
          if (result.parcels != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.md),
            _ParcelsCard(result: result),
            const SizedBox(height: EcomsbdSpacing.md),
            RtoObservationsCard(observations: result.observations),
            const SizedBox(height: EcomsbdSpacing.md),
            RtoRecentCard(recent: result.recent),
          ],
          const SizedBox(height: EcomsbdSpacing.md),
          _WhyCard(result: result),
        ],
        if (result == null && error == null && !_busy) ...<Widget>[
          const SizedBox(height: EcomsbdSpacing.lg),
          EmptyState(
            icon: Icons.shield_outlined,
            title: context.tr('rc.emptyTitle'),
            message: context.tr('rc.emptyBody'),
          ),
        ],
      ],
    );
  }
}

/// The band, and immediately under it what made a band possible at all.
class _VerdictCard extends StatelessWidget {
  const _VerdictCard({required this.result});

  final RiskCheck result;

  @override
  Widget build(BuildContext context) {
    final name = result.name;
    return StrongGlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Row(
            children: <Widget>[
              Expanded(
                child: Text(
                  name == null || name.isEmpty
                      ? context.tr('rc.unknownCustomer')
                      : name,
                  style: EcomsbdType.sectionTitle,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: EcomsbdSpacing.xs),
              RiskBadge(level: result.level),
            ],
          ),
          if (result.phoneMasked != null) ...<Widget>[
            const SizedBox(height: 2),
            Text(
              result.phoneMasked!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ],
          const SizedBox(height: EcomsbdSpacing.sm),
          Text(
            result.hasEnoughHistory
                ? context.tr('rc.bandedFrom', <String, Object?>{
                    'count': '${result.terminalCount}',
                  })
                : context.tr('rc.notEnough'),
            style: EcomsbdType.body,
          ),
          if (result.checksRemaining != null) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Text(
              context.tr('rc.checksLeft', <String, Object?>{
                'count': '${result.checksRemaining}',
              }),
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted2),
            ),
          ],
        ],
      ),
    );
  }
}

/// The numbers the band came from, shown whether or not a band was possible.
class _HistoryCard extends StatelessWidget {
  const _HistoryCard({required this.result});

  final RiskCheck result;

  @override
  Widget build(BuildContext context) {
    final percent = result.successPercent;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('rc.historyTitle'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.sm),
          _Stat(
            label: context.tr('rc.totalOrders'),
            value: '${result.orderCount}',
          ),
          _Stat(
            label: context.tr('rc.delivered'),
            value: '${result.deliveredCount}',
          ),
          _Stat(
            label: context.tr('rc.returned'),
            value: '${result.returnedCount}',
          ),
          _Stat(
            label: context.tr('rc.cancelled'),
            value: '${result.cancelledCount}',
          ),
          _Stat(
            label: context.tr('rc.successRate'),
            // "No history yet" rather than 0%: a customer who has finished
            // nothing has not failed anything.
            value: percent == null ? context.tr('risk.unknown') : '$percent%',
          ),
          if (result.lastOrderAt != null)
            _Stat(
              label: context.tr('rc.lastOrder'),
              value: formatRelative(result.lastOrderAt),
            ),
        ],
      ),
    );
  }
}

/// The same history counted as parcels: what the RTO screens use.
///
/// Kept beside the order-based band rather than replacing it: the band is the
/// V1 rule (cancellations count against it), the RTO line is parcels that
/// went out and came back. Both are facts; neither is hidden.
class _ParcelsCard extends StatelessWidget {
  const _ParcelsCard({required this.result});

  final RiskCheck result;

  @override
  Widget build(BuildContext context) {
    final parcels = result.parcels!;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('rc.parcelsTitle'), style: EcomsbdType.bodyStrong),
          const SizedBox(height: EcomsbdSpacing.xs),
          Text(
            parcels.completed == 0
                ? context.tr('rto.noCompleted')
                : context.tr('rto.rateOf', <String, Object?>{
                    'rto': parcels.rto,
                    'completed': parcels.completed,
                  }),
            style: EcomsbdType.body,
          ),
          _Stat(label: context.tr('rc.rtoRate'), value: parcels.rateLabel),
          _Stat(
            label: context.tr('rto.courierCancelled'),
            value: '${parcels.courierCancelled}',
          ),
          _Stat(
            label: context.tr('rto.onTheWay'),
            value: '${result.inTransitCount}',
          ),
        ],
      ),
    );
  }
}

/// Why this band, in sentences tied to the numbers above it.
class _WhyCard extends StatelessWidget {
  const _WhyCard({required this.result});

  final RiskCheck result;

  @override
  Widget build(BuildContext context) {
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(context.tr('rc.whyTitle'), style: EcomsbdType.bodyStrong),
          for (final reason in result.reasons) ...<Widget>[
            const SizedBox(height: EcomsbdSpacing.xs),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                const Padding(
                  padding: EdgeInsets.only(top: 5),
                  child: Icon(
                    Icons.circle,
                    size: 5,
                    color: EcomsbdColors.muted2,
                  ),
                ),
                const SizedBox(width: EcomsbdSpacing.xs),
                Expanded(
                  child: Text(
                    context.tr(reason.labelKey),
                    style: EcomsbdType.caption.copyWith(
                      color: EcomsbdColors.muted,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Text(
              label,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.muted),
            ),
          ),
          const SizedBox(width: EcomsbdSpacing.xs),
          Text(value, style: EcomsbdType.bodyStrong),
        ],
      ),
    );
  }
}
