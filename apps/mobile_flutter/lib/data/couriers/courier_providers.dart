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

/// Whether this shop can book with a courier right now.
///
/// Three answers, not two: connected, needs reconnecting, or no account at all.
/// The middle one is a problem the seller can fix; the last one is a choice
/// they have not made yet, and manual courier mode covers both.
final canBookWithCourierProvider = Provider<AsyncValue<bool>>((ref) {
  return ref
      .watch(courierAccountProvider('steadfast'))
      .whenData((account) => account?.status.canBook ?? false);
});
