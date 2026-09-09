import '../../design/components/badges.dart';

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
  'CONFIRMED' => 'Confirm',
  'PACKED' => 'Mark packed',
  'FULFILLMENT_STARTED' => 'Hand to courier',
  'CANCELLED' => 'Cancel order',
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
  'NOT_BOOKED' => 'Not booked',
  'BOOKING' => 'Booking…',
  'BOOKING_UNKNOWN' => 'Unconfirmed — checking',
  'BOOKED' => 'Booked',
  'IN_TRANSIT' => 'In transit',
  'DELIVERED' => 'Delivered',
  'RETURNED' => 'Returned',
  _ => state,
};

/// Delivery-risk state.
///
/// "Not checked" is honest; a default of "Low risk" would read as a
/// reassurance nobody computed.
String riskLabel(String state) => switch (state) {
  'NOT_CHECKED' => 'Not checked',
  'LOW' => 'Low risk',
  'MEDIUM' => 'Medium risk',
  'HIGH' => 'High risk',
  'NO_HISTORY' => 'No history',
  _ => state,
};

/// Profit state.
///
/// Profit needs a settled delivery, a courier fee and a return outcome. Until
/// those exist the app says so rather than showing a number (sections 85, 135).
String profitLabel(String state) => switch (state) {
  'PENDING_CALCULATION' => 'Pending',
  'ESTIMATED' => 'Estimated',
  'ACTUAL' => 'Actual',
  'MISSING_COST' => 'Cost missing',
  _ => state,
};
