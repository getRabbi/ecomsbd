import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/providers.dart';
import 'courier_repository.dart';
import 'models.dart';

/// Wiring for the courier integration.
///
/// None of these is cached on disk. A stale "connected" would let a seller
/// start a booking against a revoked key, and a stale parcel status would show
/// "delivered" for one that came back — both are worse than a spinner.

final courierRepositoryProvider = Provider<CourierRepository>((ref) {
  return CourierRepository(api: ref.watch(apiClientProvider));
});

/// Every courier account this shop has connected.
final courierAccountsProvider = FutureProvider<List<CourierAccount>>((ref) {
  return ref.watch(courierRepositoryProvider).accounts();
});

/// One provider's account, or null when it has never been connected.
final courierAccountProvider = FutureProvider.family<CourierAccount?, String>((
  ref,
  provider,
) async {
  final accounts = await ref.watch(courierAccountsProvider.future);
  for (final account in accounts) {
    if (account.provider == provider) {
      return account;
    }
  }
  return null;
});

/// What a provider's documentation does and does not say.
///
/// Drives the honest copy on the connection screen — "status arrives by
/// polling because this courier documents no webhook" reads very differently
/// from a status section that is simply empty.
final providerEvidenceProvider =
    FutureProvider.family<ProviderEvidence, String>((ref, provider) {
      return ref.watch(courierRepositoryProvider).evidence(provider);
    });

/// A parcel's booking attempts and provider observations.
final consignmentTrackingProvider =
    FutureProvider.family<ConsignmentTracking, String>((ref, consignmentId) {
      return ref.watch(courierRepositoryProvider).tracking(consignmentId);
    });

/// Return requests raised for a parcel.
final courierReturnsProvider =
    FutureProvider.family<List<CourierReturnRequest>, String>((
      ref,
      consignmentId,
    ) {
      return ref.watch(courierRepositoryProvider).returns(consignmentId);
    });

/// Payments synced from the courier, newest first.
final providerPaymentsProvider = FutureProvider<List<ProviderPayment>>((ref) {
  return ref.watch(courierRepositoryProvider).payments();
});


/// Every courier ecomsbd knows about, with its connect form.
///
/// Drives the accounts screen: one card per connectable courier, rendered from
/// the server's declaration rather than from a list compiled into the app.
final courierProvidersProvider = FutureProvider<List<CourierProviderInfo>>((
  ref,
) {
  return ref.watch(courierRepositoryProvider).providers();
});

/// The pickup stores on a connected courier account.
///
/// Not cached: a store added in the courier's panel a minute ago should appear
/// when the seller opens the picker.
final providerStoresProvider =
    FutureProvider.family<List<CourierStore>, String>((ref, provider) {
      return ref.watch(courierRepositoryProvider).stores(provider);
    });

/// The callback URL and webhook state for a connected courier account.
final webhookSetupProvider = FutureProvider.family<WebhookSetup, String>((
  ref,
  provider,
) {
  return ref.watch(courierRepositoryProvider).webhookSetup(provider);
});

/// Which couriers this shop can book with right now.
///
/// The booking sheet's source of truth. Not cached: a courier that went down,
/// or a pickup store chosen a minute ago, must be reflected before a seller
/// commits a parcel to it.
final bookableCouriersProvider = FutureProvider<List<BookableCourier>>((ref) {
  return ref.watch(courierRepositoryProvider).bookable();
});

/// Whether this shop can book with *any* courier right now.
///
/// Replaces the V1 question "is Steadfast connected", which stopped being the
/// right question the moment a second courier existed.
final canBookWithAnyCourierProvider = Provider<AsyncValue<bool>>((ref) {
  return ref
      .watch(bookableCouriersProvider)
      .whenData((rows) => rows.any((row) => row.bookable));
});
