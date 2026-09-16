import '../../design/components/badges.dart';
import '../../l10n/app_locale.dart';
import '../../l10n/app_strings.dart';

/// These labels are produced by top-level functions with no `BuildContext`,
/// so they resolve against the active locale directly — the same approach
/// `formatRelative` uses. Changing language rebuilds the tree, so the next
/// paint is already in the new language.
String _t(String key) => AppStrings(activeAppLocale).t(key);

/// Seller-facing wording for the order state machine.
///
/// The transitions mirror `ORDER_TRANSITIONS` in `backend/app/orders/models.py`.
/// They are duplicated here only to decide which buttons to *offer*; the server
/// is the authority and rejects an illegal move regardless of what this file
/// says (master spec section 10.1).
const Map<String, List<String>> orderTransitions = <String, List<String>>{
  'DRAFT': <String>['CONFIRMED', 'CANCELLED'],
  'CONFIRMED': <String>['PACKED', 'CANCELLED'],
  'PACKED': <String>['FULFILLMENT_STARTED', 'CANCELLED'],
  // FULFILLMENT_STARTED onwards is driven by courier events, not by taps.
  'FULFILLMENT_STARTED': <String>[],
  'COMPLETED': <String>[],
  'CANCELLED': <String>[],
};

List<String> nextStatuses(String status) =>
    orderTransitions[status] ?? const <String>[];

String statusActionLabel(String status) => switch (status) {
  'CONFIRMED' => _t('status.confirm'),
  'PACKED' => _t('status.markPacked'),
  'FULFILLMENT_STARTED' => _t('status.handToCourier'),
  'CANCELLED' => _t('status.cancelOrder'),
  _ => status,
};

Tone orderStatusTone(String status) => switch (status) {
  'DRAFT' => Tone.neutral,
  'CONFIRMED' => Tone.info,
  'PACKED' => Tone.info,
  'FULFILLMENT_STARTED' => Tone.warning,
  'COMPLETED' => Tone.good,
  'CANCELLED' => Tone.bad,
  _ => Tone.neutral,
};

/// Courier state.
///
/// `NOT_BOOKED` is the only value Phase B can produce, and it is shown as such.
/// Inventing a courier state for an order nobody has booked would be a lie the
/// seller could act on (master spec section 62.17).
String fulfillmentLabel(String state) => switch (state) {
  'NOT_BOOKED' => _t('status.notBooked'),
  'BOOKING' => _t('status.booking'),
  'BOOKING_UNKNOWN' => _t('status.unconfirmedChecking'),
  'BOOKED' => _t('status.booked'),
  'IN_TRANSIT' => _t('status.inTransit'),
  'DELIVERED' => _t('status.delivered'),
  'RETURNED' => _t('status.returned'),
  _ => state,
};

/// Delivery-risk state.
///
/// "Not checked" is honest; a default of "Low risk" would read as a
/// reassurance nobody computed.
String riskLabel(String state) => switch (state) {
  'NOT_CHECKED' => _t('status.notChecked'),
  'LOW' => _t('risk.low'),
  'MEDIUM' => _t('risk.medium'),
  'HIGH' => _t('risk.high'),
  'NO_HISTORY' => _t('risk.unknown'),
  _ => state,
};

/// Profit state.
///
/// Profit needs a settled delivery, a courier fee and a return outcome. Until
/// those exist the app says so rather than showing a number (sections 85, 135).
String profitLabel(String state) => switch (state) {
  'PENDING_CALCULATION' => _t('status.pending'),
  'ESTIMATED' => _t('status.estimated'),
  'ACTUAL' => _t('status.actual'),
  'MISSING_COST' => _t('status.costMissing'),
  _ => state,
};
