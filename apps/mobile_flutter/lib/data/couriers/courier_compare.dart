import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:meta/meta.dart';

import '../../core/api/api_error.dart';
import '../../core/money.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';
import '../analytics/analytics_providers.dart';
import 'courier_providers.dart';
import 'courier_repository.dart';
import 'models.dart';

/// Courier cost comparison.
///
/// Two kinds of rate, never mixed on one screen:
///
/// * [RateOrigin.courierQuote] — what a connected courier itself says booking
///   one specific order would cost (`/couriers/orders/{id}/quote`). Real.
/// * [RateOrigin.sample] — a published-style rate card bundled with the app,
///   for comparing before an order exists. It is labelled as sample data
///   everywhere it is shown, because it is not the seller's contract rate.
///
/// TODO(courier-rates): replace [SampleRateSource] with a merchant-contract
/// rate endpoint once the backend stores each shop's negotiated rates.
enum RateOrigin { courierQuote, sample }

/// Delivery zone for the order-less comparison.
enum DeliveryZone { insideDhaka, dhakaSuburb, outsideDhaka }

/// What the seller asked to compare.
@immutable
class CompareRequest {
  const CompareRequest({
    this.zone = DeliveryZone.insideDhaka,
    this.weightKg = 1,
    this.cod = const Money.zero(),
    this.orderId,
  });

  final DeliveryZone zone;

  /// Upper bound of the weight band, in whole kilograms.
  final int weightKg;
  final Money cod;

  /// When set, rates are the connected couriers' own quotes for this order.
  final String? orderId;

  CompareRequest copyWith({DeliveryZone? zone, int? weightKg, Money? cod}) =>
      CompareRequest(
        zone: zone ?? this.zone,
        weightKg: weightKg ?? this.weightKg,
        cod: cod ?? this.cod,
        orderId: orderId,
      );

  @override
  bool operator ==(Object other) =>
      other is CompareRequest &&
      other.zone == zone &&
      other.weightKg == weightKg &&
      other.cod == cod &&
      other.orderId == orderId;

  @override
  int get hashCode => Object.hash(zone, weightKg, cod, orderId);
}

/// One courier's estimated cost for the request.
@immutable
class CourierRateEstimate {
  const CourierRateEstimate({
    required this.provider,
    required this.displayName,
    required this.origin,
    this.deliveryFee,
    this.codFee,
    this.returnFee,
    this.returnFeeIsShopAverage = false,
    this.eta,
    this.connected = false,
    this.bookable = false,
    this.unavailableReason,
    this.rtoRateBps,
    this.rtoSampleSufficient = false,
    this.shipments,
  });

  final String provider;
  final String displayName;
  final RateOrigin origin;
  final Money? deliveryFee;
  final Money? codFee;

  /// Charge when the parcel comes back. Null when neither the courier nor the
  /// shop's own history says.
  final Money? returnFee;

  /// True when [returnFee] is the shop's own average return cost rather than
  /// this courier's rate.
  final bool returnFeeIsShopAverage;

  /// Delivery time as the rate card states it, e.g. "1–3 days".
  final String? eta;

  /// Whether the shop has an account with this courier.
  final bool connected;

  /// Whether a booking can be sent to this courier right now.
  final bool bookable;

  /// Why the courier gave no price, when it gave none.
  final String? unavailableReason;

  /// The shop's own return rate with this courier, in basis points.
  final int? rtoRateBps;
  final bool rtoSampleSufficient;
  final int? shipments;

  bool get hasPrice => deliveryFee != null;

  Money? get total => deliveryFee == null
      ? null
      : Money(deliveryFee!.paisa + (codFee?.paisa ?? 0));

  /// Expected cost of one *successful* delivery once returns are paid for.
  ///
  /// With return rate r: each shipment costs (1-r)·(delivery+COD fee) +
  /// r·(delivery+return fee) on average, and only (1-r) of shipments succeed.
  /// Null unless the price, the return charge and a sufficient return-rate
  /// sample are all known — a figure built on a guess is not shown.
  Money? get costPerSuccess {
    final delivery = deliveryFee?.paisa;
    final back = returnFee?.paisa;
    final bps = rtoRateBps;
    if (delivery == null || back == null || bps == null) return null;
    if (!rtoSampleSufficient || bps >= 10000) return null;
    final r = bps / 10000;
    final perShipment =
        (1 - r) * (delivery + (codFee?.paisa ?? 0)) + r * (delivery + back);
    return Money((perShipment / (1 - r)).round());
  }

  CourierRateEstimate withHistory({
    int? rtoRateBps,
    bool rtoSampleSufficient = false,
    int? shipments,
    Money? shopReturnAverage,
  }) {
    final useAverage = returnFee == null && shopReturnAverage != null;
    return CourierRateEstimate(
      provider: provider,
      displayName: displayName,
      origin: origin,
      deliveryFee: deliveryFee,
      codFee: codFee,
      returnFee: useAverage ? shopReturnAverage : returnFee,
      returnFeeIsShopAverage: useAverage || returnFeeIsShopAverage,
      eta: eta,
      connected: connected,
      bookable: bookable,
      unavailableReason: unavailableReason,
      rtoRateBps: rtoRateBps,
      rtoSampleSufficient: rtoSampleSufficient,
      shipments: shipments,
    );
  }
}

/// The result shown on the comparison screen.
@immutable
class CourierComparison {
  const CourierComparison({required this.origin, required this.estimates});

  final RateOrigin origin;

  /// Priced couriers first, cheapest first; unpriced ones after.
  final List<CourierRateEstimate> estimates;

  CourierRateEstimate? get lowestTotal {
    final priced = estimates.where((e) => e.hasPrice);
    return priced.isEmpty ? null : priced.first;
  }

  CourierRateEstimate? get lowestPerSuccess {
    CourierRateEstimate? best;
    for (final estimate in estimates) {
      final cost = estimate.costPerSuccess;
      if (cost == null) continue;
      if (best == null || cost.paisa < best.costPerSuccess!.paisa) {
        best = estimate;
      }
    }
    return best;
  }
}

/// Where rates come from. A new source (merchant contract rates, a rate API)
/// implements this and nothing on the screen changes.
abstract class CourierRateSource {
  RateOrigin get origin;

  Future<List<CourierRateEstimate>> estimates(
    CompareRequest request,
    List<BookableCourier> couriers,
  );
}

/// The connected couriers' own quotes for one order.
class QuoteRateSource implements CourierRateSource {
  const QuoteRateSource(this.repository);

  final CourierRepository repository;

  @override
  RateOrigin get origin => RateOrigin.courierQuote;

  @override
  Future<List<CourierRateEstimate>> estimates(
    CompareRequest request,
    List<BookableCourier> couriers,
  ) async {
    final orderId = request.orderId!;
    final bookable = couriers.where((c) => c.bookable).toList();
    return Future.wait(<Future<CourierRateEstimate>>[
      for (final courier in bookable) _quote(orderId, courier),
    ]);
  }

  Future<CourierRateEstimate> _quote(
    String orderId,
    BookableCourier courier,
  ) async {
    try {
      final quote = await repository.quote(orderId, provider: courier.provider);
      return CourierRateEstimate(
        provider: courier.provider,
        displayName: courier.displayName,
        origin: RateOrigin.courierQuote,
        deliveryFee: quote.available && quote.deliveryFeePaisa != null
            ? Money(quote.deliveryFeePaisa!)
            : null,
        codFee: quote.codFeePaisa == null ? null : Money(quote.codFeePaisa!),
        connected: true,
        bookable: true,
        unavailableReason: quote.available ? null : quote.reason,
      );
    } on ApiError catch (error) {
      return CourierRateEstimate(
        provider: courier.provider,
        displayName: courier.displayName,
        origin: RateOrigin.courierQuote,
        connected: true,
        bookable: true,
        unavailableReason: error.displayMessage,
      );
    }
  }
}

/// Bundled sample rates. Not live, not the seller's contract.
class SampleRateSource implements CourierRateSource {
  const SampleRateSource();

  @override
  RateOrigin get origin => RateOrigin.sample;

  /// (delivery, minimum COD fee, return) in taka per zone, for up to 1 kg.
  static const Map<String, _SampleCard> _cards = <String, _SampleCard>{
    'steadfast': _SampleCard(
      'Steadfast',
      <int>[65, 85, 120],
      <int>[10, 10, 10],
      <int>[50, 60, 75],
      (1, 3),
    ),
    'pathao': _SampleCard(
      'Pathao',
      <int>[70, 90, 120],
      <int>[10, 10, 15],
      <int>[50, 60, 80],
      (1, 3),
    ),
    'redx': _SampleCard(
      'RedX',
      <int>[75, 95, 125],
      <int>[10, 12, 12],
      <int>[55, 65, 80],
      (1, 4),
    ),
    'paperfly': _SampleCard(
      'Paperfly',
      <int>[70, 90, 130],
      <int>[12, 12, 15],
      <int>[55, 65, 85],
      (1, 4),
    ),
  };

  @override
  Future<List<CourierRateEstimate>> estimates(
    CompareRequest request,
    List<BookableCourier> couriers,
  ) async {
    final zone = request.zone.index;
    return <CourierRateEstimate>[
      for (final entry in _cards.entries)
        () {
          final card = entry.value;
          final match = _match(entry.key, couriers);
          final delivery =
              card.delivery[zone] + (request.weightKg - 1).clamp(0, 9) * 20;
          // 1% of COD, with the card's minimum.
          final percent = (request.cod.paisa / 100).round();
          final codFee = percent > card.codMin[zone] * 100
              ? percent
              : card.codMin[zone] * 100;
          return CourierRateEstimate(
            provider: match?.provider ?? entry.key,
            displayName: match?.displayName ?? card.name,
            origin: RateOrigin.sample,
            deliveryFee: Money(delivery * 100),
            codFee: Money(codFee),
            returnFee: Money(card.returns[zone] * 100),
            eta: AppStrings(activeAppLocale).t('cmp.etaDays', <String, Object?>{
              'min': card.eta.$1,
              'max': card.eta.$2,
            }),
            // Listed by the server is not the same as connected: a courier
            // the shop never set up comes back with a "not connected" block.
            connected:
                match != null &&
                match.block != BookableBlock.notConnected &&
                match.block != BookableBlock.notEnabled,
            bookable: match?.bookable ?? false,
          );
        }(),
    ];
  }

  static BookableCourier? _match(String key, List<BookableCourier> couriers) {
    for (final courier in couriers) {
      if (courier.provider.toLowerCase() == key) return courier;
    }
    return null;
  }
}

class _SampleCard {
  const _SampleCard(
    this.name,
    this.delivery,
    this.codMin,
    this.returns,
    this.eta,
  );

  final String name;
  final List<int> delivery;
  final List<int> codMin;
  final List<int> returns;

  /// Delivery time in days, (fastest, slowest).
  final (int, int) eta;
}

/// The comparison for a request, with the shop's own history folded in.
///
/// History is best-effort: a plan that does not include RTO analytics, or an
/// offline phone, still gets prices — just without the per-success figure.
final courierComparisonProvider = FutureProvider.autoDispose
    .family<CourierComparison, CompareRequest>((ref, request) async {
      final repository = ref.watch(courierRepositoryProvider);
      var couriers = const <BookableCourier>[];
      try {
        // Watched, not fetched: typing a COD amount makes a new request per
        // keystroke, and the connected couriers do not change between them.
        couriers = await ref.watch(bookableCouriersProvider.future);
      } on ApiError {
        // Unknown connections: compare sample rates without "connected" marks.
      }
      final CourierRateSource source =
          request.orderId != null && couriers.any((c) => c.bookable)
          ? QuoteRateSource(repository)
          : const SampleRateSource();
      var origin = source.origin;
      var estimates = await source.estimates(request, couriers);
      if (origin == RateOrigin.courierQuote &&
          !estimates.any((estimate) => estimate.hasPrice)) {
        // No connected courier priced this order (Steadfast has no quote
        // API): compare on the labelled sample rates rather than a card of
        // dashes. Connected couriers stay marked and bookable.
        origin = RateOrigin.sample;
        estimates = await const SampleRateSource().estimates(request, couriers);
      }

      final history = <String, ({int? bps, bool sufficient, int shipments})>{};
      try {
        final report = (await ref.watch(rtoCouriersProvider.future)).value;
        for (final line in report.items) {
          history[line.provider.toLowerCase()] = (
            bps: line.counts.rtoRateBps,
            sufficient: line.counts.sufficient,
            shipments: line.counts.completed,
          );
        }
      } on ApiError {
        // No history available.
      }
      Money? returnAverage;
      try {
        final returns = (await ref.watch(returnReportProvider.future)).value;
        if (returns.returnCount > 0) {
          returnAverage = Money(
            returns.returnDeliveryCost.paisa ~/ returns.returnCount,
          );
        }
      } on ApiError {
        // No return cost history.
      }

      final merged = <CourierRateEstimate>[
        for (final estimate in estimates)
          () {
            final line = history[estimate.provider.toLowerCase()];
            return estimate.withHistory(
              rtoRateBps: line?.bps,
              rtoSampleSufficient: line?.sufficient ?? false,
              shipments: line?.shipments,
              shopReturnAverage: returnAverage,
            );
          }(),
      ];
      merged.sort((a, b) {
        final at = a.total?.paisa;
        final bt = b.total?.paisa;
        if (at == null && bt == null) return 0;
        if (at == null) return 1;
        if (bt == null) return -1;
        return at.compareTo(bt);
      });
      return CourierComparison(origin: origin, estimates: merged);
    });
