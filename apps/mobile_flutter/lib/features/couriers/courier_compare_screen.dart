import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../data/commerce/models.dart';
import '../../data/couriers/courier_compare.dart';
import '../../design/components/badges.dart';
import '../../design/components/seller_blocks.dart';
import '../../design/components/states.dart';
import '../../design/components/surfaces.dart';
import '../../design/tokens.dart';
import '../../l10n/app_strings.dart';
import '../shared/data_state.dart';

/// Compare Courier Cost.
///
/// Opened from Home, Money, More and an order's detail. With an order and a
/// connected courier, the prices are each courier's own quote for that order.
/// Without, they are bundled sample rates and the screen says so in a banner
/// that cannot be scrolled past unseen — the seller must never mistake a
/// sample for their contract rate.
///
/// Resolves to the provider the seller chose to book with, when opened for an
/// order; the caller opens its booking sheet with that courier selected.
class CourierCompareScreen extends ConsumerStatefulWidget {
  const CourierCompareScreen({super.key, this.order});

  final SellerOrder? order;

  static Future<String?> open(BuildContext context, {SellerOrder? order}) {
    return Navigator.of(context).push<String>(
      MaterialPageRoute<String>(
        builder: (_) => CourierCompareScreen(order: order),
      ),
    );
  }

  @override
  ConsumerState<CourierCompareScreen> createState() =>
      _CourierCompareScreenState();
}

class _CourierCompareScreenState extends ConsumerState<CourierCompareScreen> {
  late CompareRequest _request = CompareRequest(
    zone: _zoneFor(widget.order),
    cod: widget.order?.codAmount ?? const Money.zero(),
    orderId: widget.order?.id,
  );

  RateOrigin? _lastOrigin;

  late final TextEditingController _cod = TextEditingController(
    text: _request.cod.isZero ? '' : '${_request.cod.paisa ~/ 100}',
  );

  static DeliveryZone _zoneFor(SellerOrder? order) {
    final place =
        '${order?.deliveryDistrict ?? ''} ${order?.deliveryArea ?? ''}'
            .toLowerCase();
    if (order == null || place.trim().isEmpty || place.contains('dhaka')) {
      return DeliveryZone.insideDhaka;
    }
    return DeliveryZone.outsideDhaka;
  }

  @override
  void dispose() {
    _cod.dispose();
    super.dispose();
  }

  void _setCod(String raw) {
    final taka = int.tryParse(normalizeDigits(raw).replaceAll(',', '').trim());
    setState(() => _request = _request.copyWith(cod: Money((taka ?? 0) * 100)));
  }

  @override
  Widget build(BuildContext context) {
    final order = widget.order;
    final comparison = ref.watch(courierComparisonProvider(_request));
    // Each COD keystroke is a new request that starts out loading; keep the
    // last known origin so the banner and the focused COD field stay put.
    final origin = _lastOrigin = comparison.valueOrNull?.origin ?? _lastOrigin;
    final showInputs = order == null || origin == RateOrigin.sample;

    return DetailScaffold(
      eyebrow: context.tr('cmp.eyebrow'),
      title: context.tr('cmp.title'),
      subtitle: order == null
          ? context.tr('cmp.subtitle')
          : context.tr('cmp.forOrder', <String, Object?>{
              'number': order.orderNumber,
              'cod': order.codAmount.format(),
            }),
      children: <Widget>[
        // The sample warning sits above everything it qualifies.
        if (origin == RateOrigin.sample)
          AlertStrip(
            icon: Icons.info_outline_rounded,
            tone: Tone.warning,
            title: context.tr('cmp.sampleTitle'),
            detail: context.tr('cmp.sampleBody'),
          )
        else if (origin == RateOrigin.courierQuote)
          AlertStrip(
            icon: Icons.verified_outlined,
            tone: Tone.info,
            title: context.tr('cmp.liveTitle'),
            detail: context.tr('cmp.liveBody'),
          ),
        if (showInputs) ...<Widget>[
          const SizedBox(height: 12),
          GlassCard(
            padding: const EdgeInsets.all(14),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Expanded(
                      child: _Field(
                        label: context.tr('cmp.area'),
                        child: _Select<DeliveryZone>(
                          value: _request.zone,
                          items: <(DeliveryZone, String)>[
                            for (final zone in DeliveryZone.values)
                              (zone, context.tr('cmp.zone.${zone.name}')),
                          ],
                          onChanged: (zone) => setState(
                            () => _request = _request.copyWith(zone: zone),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _Field(
                        label: context.tr('cmp.weight'),
                        child: _Select<int>(
                          value: _request.weightKg,
                          items: <(int, String)>[
                            for (final kg in const <int>[1, 2, 3])
                              (kg, context.tr('cmp.weight.$kg')),
                          ],
                          onChanged: (kg) => setState(
                            () => _request = _request.copyWith(weightKg: kg),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                _Field(
                  label: context.tr('cmp.cod'),
                  child: TextField(
                    controller: _cod,
                    keyboardType: TextInputType.number,
                    inputFormatters: <TextInputFormatter>[
                      FilteringTextInputFormatter.allow(RegExp(r'[0-9০-৯,]')),
                    ],
                    style: EcomsbdType.bodyStrong,
                    decoration: _fieldDecoration(prefixText: '৳ '),
                    onChanged: _setCod,
                  ),
                ),
              ],
            ),
          ),
        ],
        SectionHeader(
          title: context.tr('cmp.results'),
          subtitle: context.tr('cmp.resultsSub'),
        ),
        comparison.when(
          loading: () => const ContentLoader(minHeight: 160),
          error: (error, _) => ErrorStateCard(
            error: error is ApiError ? error : ApiError.unexpected(error),
            onRetry: () => ref.invalidate(courierComparisonProvider(_request)),
          ),
          data: (result) {
            if (result.estimates.isEmpty) {
              return EmptyState(
                icon: Icons.local_shipping_outlined,
                title: context.tr('cmp.emptyTitle'),
                message: context.tr('cmp.emptyBody'),
              );
            }
            final lowest = result.lowestTotal;
            final bestPerSuccess = result.lowestPerSuccess;
            return Column(
              children: <Widget>[
                for (final estimate in result.estimates)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: _RateCard(
                      estimate: estimate,
                      isLowest: identical(estimate, lowest),
                      isBestPerSuccess: identical(estimate, bestPerSuccess),
                      onBook: order != null && estimate.bookable
                          ? () => Navigator.of(context).pop(estimate.provider)
                          : null,
                    ),
                  ),
              ],
            );
          },
        ),
        const SizedBox(height: 4),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: const Color(0xFFEEF7FF),
            borderRadius: EcomsbdRadii.row,
            border: Border.all(color: const Color(0xFFDBEAFF)),
          ),
          child: Text(
            context.tr('cmp.callout'),
            style: EcomsbdType.caption.copyWith(
              color: const Color(0xFF38526C),
              height: 1.5,
            ),
          ),
        ),
      ],
    );
  }
}

class _RateCard extends StatelessWidget {
  const _RateCard({
    required this.estimate,
    required this.isLowest,
    required this.isBestPerSuccess,
    this.onBook,
  });

  final CourierRateEstimate estimate;
  final bool isLowest;
  final bool isBestPerSuccess;
  final VoidCallback? onBook;

  @override
  Widget build(BuildContext context) {
    final notKnown = context.tr('cmp.notKnown');
    final bps = estimate.rtoRateBps;
    final rto = bps == null || !estimate.rtoSampleSufficient
        ? context.tr('common.notEnoughData')
        : '${(bps / 100).toStringAsFixed(1)}%';
    final perSuccess = estimate.costPerSuccess;

    return GlassCard(
      padding: const EdgeInsets.all(15),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            children: <Widget>[
              CourierMark(
                provider: estimate.provider,
                name: estimate.displayName,
              ),
              const SizedBox(width: 11),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      estimate.displayName,
                      style: EcomsbdType.bodyStrong.copyWith(fontSize: 15),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      <String>[
                        if (estimate.connected)
                          context.tr('cmp.connected')
                        else
                          context.tr('cmp.notConnected'),
                        if (estimate.eta != null) estimate.eta!,
                      ].join(' · '),
                      style: EcomsbdType.caption.copyWith(
                        color: estimate.connected
                            ? EcomsbdColors.green
                            : EcomsbdColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
              if (isLowest)
                _Tag(label: context.tr('cmp.lowest'), tone: Tone.good),
            ],
          ),
          if (isBestPerSuccess) ...<Widget>[
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: _Tag(
                label: context.tr('cmp.bestPerSuccess'),
                tone: Tone.info,
              ),
            ),
          ],
          if (estimate.unavailableReason != null) ...<Widget>[
            const SizedBox(height: 8),
            Text(
              estimate.unavailableReason!,
              style: EcomsbdType.caption.copyWith(color: EcomsbdColors.red),
            ),
          ],
          const SizedBox(height: 12),
          _FactWrap(
            facts: <(String, String, bool)>[
              (
                context.tr('cmp.delivery'),
                estimate.deliveryFee?.format() ?? notKnown,
                false,
              ),
              (
                context.tr('cmp.codFee'),
                estimate.codFee?.format() ?? notKnown,
                false,
              ),
              (
                context.tr('cmp.total'),
                estimate.total?.format() ?? notKnown,
                true,
              ),
              (
                estimate.returnFeeIsShopAverage
                    ? context.tr('cmp.returnAvg')
                    : context.tr('cmp.returnFee'),
                estimate.returnFee?.format() ?? notKnown,
                false,
              ),
              (context.tr('cmp.yourRto'), rto, false),
              (
                context.tr('cmp.perSuccess'),
                perSuccess?.format() ?? notKnown,
                false,
              ),
            ],
          ),
          if (onBook != null) ...<Widget>[
            const SizedBox(height: 12),
            CardButton(
              label: context.tr('cmp.bookWith', <String, Object?>{
                'courier': estimate.displayName,
              }),
              icon: Icons.local_shipping_outlined,
              onPressed: onBook,
            ),
          ],
        ],
      ),
    );
  }
}

/// `.tag` — a small green or blue label on a rate card.
class _Tag extends StatelessWidget {
  const _Tag({required this.label, required this.tone});

  final String label;
  final Tone tone;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: tone == Tone.good ? const Color(0xFFEAF8F1) : tone.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        label,
        style: EcomsbdType.chip.copyWith(
          fontSize: 10.5,
          fontWeight: FontWeight.w800,
          color: tone.ink,
        ),
      ),
    );
  }
}

/// `.field` — a small label over a compact control.
class _Field extends StatelessWidget {
  const _Field({required this.label, required this.child});

  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        Text(
          label,
          style: EcomsbdType.chip.copyWith(
            fontSize: 11,
            fontWeight: FontWeight.w800,
            color: const Color(0xFF8B98A6),
          ),
        ),
        const SizedBox(height: 5),
        child,
      ],
    );
  }
}

InputDecoration _fieldDecoration({String? prefixText}) {
  const border = OutlineInputBorder(
    borderRadius: BorderRadius.all(Radius.circular(13)),
    borderSide: BorderSide(color: EcomsbdColors.line),
  );
  return InputDecoration(
    isDense: true,
    filled: true,
    fillColor: Colors.white,
    prefixText: prefixText,
    contentPadding: const EdgeInsets.symmetric(horizontal: 11, vertical: 12),
    border: border,
    enabledBorder: border,
    focusedBorder: border.copyWith(
      borderSide: const BorderSide(color: EcomsbdColors.orange),
    ),
  );
}

/// A compact dropdown in the prototype's select style.
class _Select<T> extends StatelessWidget {
  const _Select({
    required this.value,
    required this.items,
    required this.onChanged,
  });

  final T value;
  final List<(T, String)> items;
  final ValueChanged<T> onChanged;

  @override
  Widget build(BuildContext context) {
    return DropdownButtonFormField<T>(
      initialValue: value,
      isExpanded: true,
      isDense: true,
      decoration: _fieldDecoration(),
      style: EcomsbdType.bodyStrong.copyWith(color: EcomsbdColors.ink),
      borderRadius: BorderRadius.circular(13),
      items: <DropdownMenuItem<T>>[
        for (final (item, label) in items)
          DropdownMenuItem<T>(
            value: item,
            child: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
          ),
      ],
      onChanged: (next) {
        if (next != null) onChanged(next);
      },
    );
  }
}

/// Label/value pairs, three to a row.
class _FactWrap extends StatelessWidget {
  const _FactWrap({required this.facts});

  /// Label, value, and whether the value is the one to read first.
  final List<(String, String, bool)> facts;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        const gap = EcomsbdSpacing.xs;
        final width = (constraints.maxWidth - gap * 2) / 3;
        return Wrap(
          spacing: gap,
          runSpacing: gap,
          children: <Widget>[
            for (final (label, value, strong) in facts)
              Container(
                width: width,
                padding: const EdgeInsets.all(9),
                decoration: BoxDecoration(
                  color: strong ? EcomsbdColors.peach : EcomsbdColors.cell,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      label,
                      style: EcomsbdType.caption.copyWith(
                        color: EcomsbdColors.muted,
                        fontSize: 11,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    FittedBox(
                      fit: BoxFit.scaleDown,
                      alignment: Alignment.centerLeft,
                      child: Text(
                        value,
                        style: EcomsbdType.bodyStrong.copyWith(
                          fontSize: 13.5,
                          fontWeight: strong ? FontWeight.w900 : null,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}
